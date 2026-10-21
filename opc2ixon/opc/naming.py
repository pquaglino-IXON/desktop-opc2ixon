"""Identifier / name generation following the IXON Cloud conventions.

IXON identifiers seen in exported CSV files are lowercase, use '-' as the only
separator and put a '-' between letters and digits, e.g.::

    F64          -> f-64
    ui64         -> ui-64
    Motor 1.Speed -> motor-1-speed

Identifiers are limited to 64 characters: longer ones are cut in the middle (head---tail)
and get a short code derived from the NodeId, so they stay unique and stable between exports.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from collections.abc import Iterable

_LETTER_DIGIT = re.compile(r"(?<=[A-Za-z])(?=[0-9])|(?<=[0-9])(?=[A-Za-z])")
_NON_ALNUM = re.compile(r"[^A-Za-z0-9]+")


def slugify_part(text: str) -> str:
    """Clean a single path element."""
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    text = _LETTER_DIGIT.sub("-", text)
    text = _NON_ALNUM.sub("-", text)
    return text.strip("-").lower()


def make_identifier(path: Iterable[str]) -> str:
    """Build an IXON identifier from the browse path elements."""
    parts = [slugify_part(p) for p in path]
    ident = "-".join(p for p in parts if p)
    return ident or "variable"


def make_name(path: Iterable[str], separator: str = ".") -> str:
    """Build the human readable variable name (full path)."""
    return separator.join(path)


MAX_IDENTIFIER = 64  # IXON skips longer identifiers on import
HEAD_LEN = 20  # characters kept from the start when shortening
HASH_LEN = 6
JOIN = "---"  # marks where the middle of a long identifier was cut


def stable_suffix(key: str, length: int = HASH_LEN) -> str:
    """Short hex code derived from ``key`` (always the same for the same key)."""
    return hashlib.sha1(key.encode("utf-8")).hexdigest()[:length]


def shorten_middle(identifier: str, budget: int) -> str:
    """Cut the middle of ``identifier`` so that it fits in ``budget`` characters.

    Keeps about HEAD_LEN characters at the start (the context: PLC, data block…), joins
    with '---' and fills the rest with the end (the variable itself). Cuts happen on '-' when that does not
    waste too many characters, so words are not split needlessly.
    """
    if len(identifier) <= budget:
        return identifier
    dashes = [i for i, c in enumerate(identifier) if c == "-"]
    # head: end on a '-' close to HEAD_LEN (between -8 and +6 characters), else hard cut
    head_target = min(HEAD_LEN, budget // 3)
    near = [d for d in dashes if head_target - 8 <= d <= head_target + 6]
    head_end = min(near, key=lambda d: abs(d - head_target)) if near else head_target
    head = identifier[:head_end].rstrip("-")
    # tail: as long as possible, starting right after a '-' if that loses <= 8 characters
    tail_len = budget - len(head) - len(JOIN)
    start = len(identifier) - tail_len
    after = [d + 1 for d in dashes if start <= d + 1 <= start + 8]
    tail = identifier[(after[0] if after else start):].lstrip("-")
    return f"{head}{JOIN}{tail}"


def assign_identifier(base: str, key: str, taken: set[str]) -> tuple[str, bool, bool]:
    """Return ``(identifier, shortened, deduplicated)`` and add it to ``taken``.

    * ``base`` short enough and free  -> used as is (unchanged behaviour);
    * ``base`` longer than 64         -> head + '---' + tail + '-' + stable code from ``key``;
    * ``base`` already used           -> '-' + stable code from ``key`` (cut if needed).

    ``key`` must identify the variable (its NodeId): the code does not depend on the order
    of the variables, so the identifier stays the same at every export.
    """
    if len(base) <= MAX_IDENTIFIER and base not in taken:
        taken.add(base)
        return base, False, False
    shortened = len(base) > MAX_IDENTIFIER
    dedup = not shortened
    for n in range(HASH_LEN, 41, 2):  # longer code in the (very unlikely) case of a clash
        code = stable_suffix(key, n)
        ident = f"{shorten_middle(base, MAX_IDENTIFIER - n - 1)}-{code}"
        if ident not in taken:
            taken.add(ident)
            return ident, shortened, dedup
    raise RuntimeError(f"cannot build a unique identifier for {base}")


def make_unique(identifier: str, taken: set[str]) -> str:
    """Return ``identifier`` or ``identifier-2``, ``-3``... so that it is not in ``taken``.

    The returned value is added to ``taken``.
    """
    candidate = identifier
    n = 2
    while candidate in taken:
        candidate = f"{identifier}-{n}"
        n += 1
    taken.add(candidate)
    return candidate
