"""Recursive browse of an OPC UA address space.

The address space is browsed level by level using batched Browse / Read requests,
which is fast even on PLCs with a few thousand nodes.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable

from asyncua import Client, ua

from ..profiles import Profile
from .client import ConnectionError_
from .model import BrowseNode
from .type_mapping import (
    ABSTRACT_TYPES,
    ENUMERATION,
    STRUCTURE,
    builtin_name,
    map_builtin,
)

HAS_PROPERTY = 46
HAS_SUBTYPE = 45
BROWSE_CHUNK = 200  # default nodes per Browse request (when the server declares no lower limit)
READ_CHUNK = 500  # default nodes per Read request
MAX_NODES_PER_READ = 11705  # Server_ServerCapabilities_OperationLimits_MaxNodesPerRead
MAX_NODES_PER_BROWSE = 11710  # Server_ServerCapabilities_OperationLimits_MaxNodesPerBrowse

log = logging.getLogger("opc2ixon.browse")

ProgressCb = Callable[[str, int], None]
CancelCb = Callable[[], bool]


class BrowseCancelled(Exception):
    pass


def nodeid_text(nid: ua.NodeId) -> str:
    t = nid.NodeIdType
    if t in (ua.NodeIdType.TwoByte, ua.NodeIdType.FourByte, ua.NodeIdType.Numeric):
        return f"ns={nid.NamespaceIndex};i={nid.Identifier}"
    if t == ua.NodeIdType.String:
        return f"ns={nid.NamespaceIndex};s={nid.Identifier}"
    if t == ua.NodeIdType.Guid:
        return f"ns={nid.NamespaceIndex};g={nid.Identifier}"
    return f"ns={nid.NamespaceIndex};b={nid.Identifier!r}"


def ixon_address(nid: ua.NodeId) -> str | None:
    """IXON accepts only numeric (i=) and string (s=) NodeIds."""
    if nid.NodeIdType in (
        ua.NodeIdType.TwoByte, ua.NodeIdType.FourByte, ua.NodeIdType.Numeric, ua.NodeIdType.String
    ):
        return nodeid_text(nid)
    return None


def _too_many(e: Exception) -> bool:
    return getattr(e, "code", None) == ua.StatusCodes.BadTooManyOperations


def _too_many_error(service: str, size: int) -> ConnectionError_:
    return ConnectionError_(
        f"The server refused a {service} request with {size} node(s): too many operations in one "
        "request (BadTooManyOperations).\n\n"
        "Set a lower value in Browse → 'Max nodes per request' in the profile (e.g. 50 or 10), "
        "or increase the request limit in the configuration of the OPC UA server."
    )


_INDEX_RE = re.compile(r"^\[?(\d+)\]?$")


class _Item:
    """Internal bookkeeping during browse."""

    __slots__ = ("node", "nid", "depth", "eu_prop", "value_rank", "dtype", "access")

    def __init__(self, node: BrowseNode, nid: ua.NodeId, depth: int):
        self.node = node
        self.nid = nid
        self.depth = depth
        self.eu_prop: ua.NodeId | None = None
        self.value_rank: int = -1
        self.dtype: ua.NodeId | None = None
        self.access: int = 1


class Browser:
    def __init__(
        self,
        client: Client,
        profile: Profile,
        progress: ProgressCb | None = None,
        cancelled: CancelCb | None = None,
    ):
        self.client = client
        self.uac = client.uaclient
        self.profile = profile
        self.progress = progress or (lambda m, n: None)
        self.cancelled = cancelled or (lambda: False)
        self._dtype_cache: dict[str, tuple[int | None, str]] = {}
        self.count = 0
        self.browse_chunk = BROWSE_CHUNK
        self.read_chunk = READ_CHUNK

    # ---- request size ------------------------------------------------------
    async def _setup_limits(self) -> None:
        """Nodes per request: the profile value, or the limits declared by the server
        (OperationLimits MaxNodesPerRead / MaxNodesPerBrowse, 0 = no limit)."""
        manual = int(getattr(self.profile, "max_nodes_per_request", 0) or 0)
        if manual > 0:
            self.browse_chunk = self.read_chunk = manual
            log.info("Nodes per request: %d (set in the profile)", manual)
            return
        try:
            values = await self.uac.read_attributes(
                [ua.NodeId(MAX_NODES_PER_READ), ua.NodeId(MAX_NODES_PER_BROWSE)], ua.AttributeIds.Value
            )
            limits = []
            for dv in values:
                v = dv.Value.Value if dv.StatusCode.is_good() and dv.Value is not None else 0
                limits.append(int(v) if isinstance(v, int) and v > 0 else 0)
            if limits[0]:
                self.read_chunk = min(READ_CHUNK, limits[0])
            if limits[1]:
                self.browse_chunk = min(BROWSE_CHUNK, limits[1])
            log.info(
                "Server limits: MaxNodesPerRead=%s MaxNodesPerBrowse=%s -> read %d / browse %d per request",
                limits[0] or "not set", limits[1] or "not set", self.read_chunk, self.browse_chunk,
            )
        except Exception as e:  # limits not readable: keep defaults, the fallback handles the rest
            log.info("Server limits not readable (%s): read %d / browse %d per request",
                     e, self.read_chunk, self.browse_chunk)

    # ---- low level batched services ---------------------------------
    async def _browse_many(self, nids: list[ua.NodeId], *, inverse_subtype=False):
        out: list[list[ua.ReferenceDescription]] = []
        i = 0
        while i < len(nids):
            chunk = nids[i : i + self.browse_chunk]
            params = ua.BrowseParameters()
            params.View = ua.ViewDescription()
            params.RequestedMaxReferencesPerNode = 0
            descs = []
            for n in chunk:
                d = ua.BrowseDescription()
                d.NodeId = n
                d.IncludeSubtypes = True
                d.ResultMask = int(ua.BrowseResultMask.All)
                if inverse_subtype:
                    d.BrowseDirection = ua.BrowseDirection.Inverse
                    d.ReferenceTypeId = ua.NodeId(HAS_SUBTYPE)
                    d.NodeClassMask = int(ua.NodeClass.DataType)
                else:
                    d.BrowseDirection = ua.BrowseDirection.Forward
                    d.ReferenceTypeId = ua.NodeId(ua.ObjectIds.HierarchicalReferences)
                    d.NodeClassMask = int(ua.NodeClass.Object) | int(ua.NodeClass.Variable)
                descs.append(d)
            params.NodesToBrowse = descs
            try:
                results = await self.uac.browse(params)
            except Exception as e:
                if _too_many(e) and len(chunk) > 1:
                    self.browse_chunk = max(1, len(chunk) // 2)
                    log.warning("BadTooManyOperations on Browse: retrying with %d nodes per request",
                                self.browse_chunk)
                    continue
                if _too_many(e):
                    raise _too_many_error("Browse", len(chunk)) from e
                raise
            i += len(chunk)
            for r in results:
                refs = list(r.References or []) if r.StatusCode.is_good() else []
                cp = r.ContinuationPoint
                while cp:
                    p = ua.BrowseNextParameters()
                    p.ReleaseContinuationPoints = False
                    p.ContinuationPoints = [cp]
                    r2 = (await self.uac.browse_next(p))[0]
                    refs.extend(r2.References or [])
                    cp = r2.ContinuationPoint
                out.append(refs)
        return out

    async def _read_many(self, nids: list[ua.NodeId], attr: ua.AttributeIds) -> list[ua.DataValue]:
        out: list[ua.DataValue] = []
        i = 0
        while i < len(nids):
            chunk = list(nids[i : i + self.read_chunk])
            try:
                out.extend(await self.uac.read_attributes(chunk, attr))
            except Exception as e:
                if _too_many(e) and len(chunk) > 1:
                    self.read_chunk = max(1, len(chunk) // 2)
                    log.warning("BadTooManyOperations on Read: retrying with %d nodes per request",
                                self.read_chunk)
                    continue
                if _too_many(e):
                    raise _too_many_error("Read", len(chunk)) from e
                raise
            i += len(chunk)
        return out

    # ---- data types ----------------------------------------------------
    async def _resolve_dtypes(self, dtypes: list[ua.NodeId]) -> None:
        """Walk HasSubtype upwards until a built-in type (ns=0, id<=29) is reached."""
        pending = {}
        for d in dtypes:
            key = d.to_string()
            if key in self._dtype_cache:
                continue
            if d.NamespaceIndex == 0 and isinstance(d.Identifier, int) and d.Identifier <= 29:
                self._dtype_cache[key] = (d.Identifier, builtin_name(d.Identifier))
                continue
            pending[key] = d
        if not pending:
            return
        names = await self._read_many(list(pending.values()), ua.AttributeIds.BrowseName)
        display = {}
        for (key, d), dv in zip(pending.items(), names, strict=False):
            try:
                display[key] = dv.Value.Value.Name
            except Exception:
                display[key] = d.to_string()
        # walk up the hierarchy
        chain = {k: d for k, d in pending.items()}
        current = dict(chain)  # original key -> node to inspect
        for _ in range(12):
            if not current:
                break
            keys = list(current)
            supers = await self._browse_many([current[k] for k in keys], inverse_subtype=True)
            nxt = {}
            for k, refs in zip(keys, supers, strict=False):
                if not refs:
                    self._dtype_cache[k] = (None, display.get(k, k))
                    continue
                sup = refs[0].NodeId
                if sup.NamespaceIndex == 0 and isinstance(sup.Identifier, int) and sup.Identifier <= 29:
                    self._dtype_cache[k] = (sup.Identifier, display.get(k, k))
                else:
                    nxt[k] = sup
            current = nxt
        for k in current:
            self._dtype_cache[k] = (None, display.get(k, k))

    # ---- main ------------------------------------------------------------
    async def run(self) -> BrowseNode:
        p = self.profile
        if p.start_node.strip():
            try:
                start = ua.NodeId.from_string(p.start_node.strip())
            except Exception as e:
                raise ValueError(f"Invalid start node '{p.start_node}': {e}") from e
        else:
            start = ua.NodeId(ua.ObjectIds.ObjectsFolder)

        await self._setup_limits()
        dn = await self._read_many([start], ua.AttributeIds.DisplayName)
        if not dn[0].StatusCode.is_good():
            raise ValueError(f"Start node {p.start_node} not found on the server.")
        root_name = dn[0].Value.Value.Text or "Objects"
        root = BrowseNode(nodeid_text(start), root_name, [], "Object")
        visited = {start.to_string()}
        level: list[_Item] = [_Item(root, start, 0)]
        variables: list[_Item] = []

        while level:
            self._check_cancel()
            self.progress(f"Browsing… {self.count} nodes found", self.count)
            refs_per_node = await self._browse_many([it.nid for it in level])
            new_items: list[_Item] = []
            for parent, refs in zip(level, refs_per_node, strict=False):
                for ref in refs:
                    nid = ref.NodeId
                    ref_type = ref.ReferenceTypeId
                    is_prop = (
                        ref_type.NamespaceIndex == 0 and ref_type.Identifier == HAS_PROPERTY
                    )
                    name = ref.BrowseName.Name if ref.BrowseName else ""
                    if is_prop:
                        if name == "EngineeringUnits":
                            parent.eu_prop = nid
                        continue
                    if p.ignore_ns0 and nid.NamespaceIndex == 0:
                        continue
                    key = nid.to_string()
                    if key in visited:
                        continue
                    visited.add(key)
                    text = (ref.DisplayName.Text if ref.DisplayName else None) or name or key
                    ncls = "Variable" if ref.NodeClass == ua.NodeClass.Variable else "Object"
                    path = self._child_path(parent.node, text)
                    child = BrowseNode(nodeid_text(nid), text, path, ncls, address=ixon_address(nid))
                    parent.node.children.append(child)
                    item = _Item(child, nid, parent.depth + 1)
                    new_items.append(item)
                    self.count += 1

            new_vars = [i for i in new_items if i.node.node_class == "Variable"]
            if new_vars:
                await self._read_variable_attrs(new_vars)
                variables.extend(new_vars)

            # decide what to browse next
            level = []
            for it in new_items:
                if it.depth >= p.max_depth:
                    continue
                if it.node.node_class == "Variable" and it.node.is_array and not p.expand_arrays:
                    continue
                level.append(it)

        self._check_cancel()
        self.progress(f"Analysing {len(variables)} variables…", self.count)
        await self._classify(variables)
        if p.units_from_server:
            await self._read_units(variables)
        _prune(root)
        return root

    def _check_cancel(self):
        if self.cancelled():
            raise BrowseCancelled()

    @staticmethod
    def _child_path(parent: BrowseNode, text: str) -> list[str]:
        if parent.node_class == "Variable" and parent.is_array and parent.path:
            # array element: "arr[3]" or "3" -> replaces the parent element
            last = parent.path[-1]
            m = _INDEX_RE.match(text)
            if m:
                return parent.path[:-1] + [f"{last}[{m.group(1)}]"]
            if text.startswith(last + "["):
                return parent.path[:-1] + [text]
        return parent.path + [text]

    async def _read_variable_attrs(self, items: list[_Item]) -> None:
        nids = [i.nid for i in items]
        dts = await self._read_many(nids, ua.AttributeIds.DataType)
        vrs = await self._read_many(nids, ua.AttributeIds.ValueRank)
        acc = await self._read_many(nids, ua.AttributeIds.UserAccessLevel)
        for it, dt, vr, ac in zip(items, dts, vrs, acc, strict=False):
            it.dtype = dt.Value.Value if dt.StatusCode.is_good() and dt.Value else None
            it.value_rank = vr.Value.Value if vr.StatusCode.is_good() and vr.Value else -1
            it.access = ac.Value.Value if ac.StatusCode.is_good() and ac.Value else 1
            it.node.is_array = isinstance(it.value_rank, int) and it.value_rank >= 0

    async def _classify(self, items: list[_Item]) -> None:
        await self._resolve_dtypes([i.dtype for i in items if i.dtype is not None])

        # variables whose type/arrayness is only known from the value
        need_value = []
        for it in items:
            builtin = self._dtype_cache.get(it.dtype.to_string(), (None, ""))[0] if it.dtype else None
            if builtin in ABSTRACT_TYPES or it.value_rank in (-2, -3):
                need_value.append(it)
        variant_of: dict[int, tuple[int | None, bool]] = {}
        if need_value:
            values = await self._read_many([i.nid for i in need_value], ua.AttributeIds.Value)
            for it, dv in zip(need_value, values, strict=False):
                try:
                    v = dv.Value
                    variant_of[id(it)] = (int(v.VariantType.value), isinstance(v.Value, list))
                except Exception:
                    variant_of[id(it)] = (None, False)

        for it in items:
            n = it.node
            builtin, tname = (None, "?")
            if it.dtype is not None:
                builtin, tname = self._dtype_cache.get(it.dtype.to_string(), (None, it.dtype.to_string()))
            if id(it) in variant_of:
                vb, varr = variant_of[id(it)]
                if builtin in ABSTRACT_TYPES and vb:
                    builtin = vb
                    tname = f"{tname}→{builtin_name(vb)}"
                if varr:
                    n.is_array = True
            n.opc_type = tname + ("[]" if n.is_array else "")
            n.ixon_type = map_builtin(builtin)
            readable = bool(int(it.access) & 1)

            has_children = bool(n.children)
            if n.is_array:
                if not self.profile.expand_arrays:
                    n.unsupported_reason = "Array (skipped by profile)"
                    n.children.clear()
                elif has_children:
                    n.unsupported_reason = "Array – select the elements"
                else:
                    n.unsupported_reason = "Array without element nodes on the server"
            elif not readable:
                n.unsupported_reason = "Not readable"
            elif n.address is None:
                n.unsupported_reason = "NodeId type not supported by IXON (only s= / i=)"
            elif n.ixon_type is None:
                if builtin == STRUCTURE or has_children:
                    n.unsupported_reason = "Structure – select the members" if has_children else (
                        "Structure without member nodes on the server"
                    )
                else:
                    n.unsupported_reason = f"Type {tname} not supported by IXON"
            if builtin == ENUMERATION and n.ixon_type is not None:
                n.opc_type += " (enum)"

    async def _read_units(self, items: list[_Item]) -> None:
        with_eu = [i for i in items if i.eu_prop is not None and i.node.exportable]
        if not with_eu:
            return
        values = await self._read_many([i.eu_prop for i in with_eu], ua.AttributeIds.Value)
        for it, dv in zip(with_eu, values, strict=False):
            try:
                eu = dv.Value.Value
                text = eu.DisplayName.Text if eu.DisplayName else ""
                it.node.unit = text or ""
            except Exception:
                pass


def _prune(node: BrowseNode) -> bool:
    """Remove Object folders that contain no variables at all. Returns True if node is kept."""
    node.children = [c for c in node.children if _prune(c)]
    if node.node_class == "Variable":
        return True
    return bool(node.children) or not node.path  # always keep root


async def browse(
    client: Client, profile: Profile, progress: ProgressCb | None = None, cancelled: CancelCb | None = None
) -> BrowseNode:
    return await Browser(client, profile, progress, cancelled).run()
