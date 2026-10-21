# opc2ixon

Desktop app (Windows / macOS) to browse an OPC UA server, select variables and export them
as an **IXON Cloud variables CSV**.

> **Disclaimer – not an official IXON product.** This tool is a personal side project of an IXON
> solution engineer, made to help customers. It is not developed, released, maintained or supported
> by IXON. It is provided "as is", without warranty of any kind; use it at your own risk. Neither the
> author nor IXON accepts any liability for its use.

## Requirements

- Python **3.10 – 3.14** (https://www.python.org/downloads/)

## Install & run

```bash
# from the project folder
python -m venv .venv
# macOS / Linux
source .venv/bin/activate
# Windows (PowerShell)
.venv\Scripts\Activate.ps1

pip install -r requirements.txt
python run.py
```

## Try it without a PLC

A demo server (Siemens-like and Beckhoff-like address space, ~530 variables) is included:

```bash
python tools/demo_server.py            # anonymous on opc.tcp://localhost:4840
python tools/demo_server.py --users    # username demo / password demo
```

Then start the app and use the profile *Demo server (localhost)*.
Secure endpoints offered by the demo server: `Basic256Sha256` with `Sign` / `SignAndEncrypt`.

## Workflow

1. **Profiles** – connection, security, browse and export options. Profiles are saved as JSON in
   the user configuration folder and can be exported/imported to share them (passwords are never
   exported; they can optionally be stored in the system keychain). Changes are stored only with
   **Save** (Ctrl+S / ⌘S); with unsaved changes the app asks before switching profile, connecting
   or quitting (*Don't Save* on Connect uses the values on screen for that connection only).
2. **Connect & Browse** – full scan of the address space in the background.
3. **Select** – all supported variables are checked by default; tree with tri-state checkboxes,
   filter, check all / uncheck all / invert (on the filtered items when a filter is active).
   Unsupported variables are shown in grey with the reason.
   Fast checking:
   - press on a checkbox and drag over other rows → they all get the same state;
   - highlight rows (drag, Shift+click, Ctrl/⌘+click), then press **Space** or click one of their
     checkboxes → all highlighted rows are toggled; right-click → *Check / Uncheck highlighted*.

   `Str len`, `Factor` and `Unit` can be edited with a double click.
   Right-click a folder → *Use as start node in this profile*.
4. **Export CSV** – preview, then save. When importing, IXON adds new variables and skips those
   whose Identifier already exists, so the full selection can be exported every time.

## IXON CSV rules implemented

| Column | Rule |
|---|---|
| Identifier | full path, lowercase, `-` between letters/digits and instead of special chars (`ui64` → `ui-64`). Max **64 characters**: longer ones are cut in the middle, `head---tail-xxxxxx`, where `xxxxxx` is a code derived from the NodeId (e.g. `plc-1-datablocksglobal---axis-x-position-actual-value-32852a`). Duplicates get the same `-xxxxxx` code. The code depends only on the NodeId, so a variable always gets the same identifier at every export. |
| Name | full path, separator configurable in the profile (default `.`), no length limit |
| Address | `ns=X;s=…` or `ns=X;i=…` (Guid / opaque NodeIds are not supported by IXON) |
| Type / Width / Signed | Boolean→bool · SByte/Byte→int 8 · (U)Int16/32/64→int · Float→float 32 · Double→float 64 · String→str · Enum→int 32 signed |
| Max string length | profile default (255), editable per variable |
| Factor | profile default (empty), editable per variable |
| Unit | empty, or EngineeringUnits read from the server (profile option) |

Not supported (grey): DateTime, ByteString, Guid, structures as a whole (members are selectable),
arrays as a whole (elements are selectable when the server exposes them as nodes, as Siemens and
Beckhoff do), non-readable variables.

## Logs and support

Every run writes a log (connection attempts, endpoints, browse statistics, exports and errors;
passwords are never logged):

- Windows: `%LOCALAPPDATA%\opc2ixon\Logs\opc2ixon.log`
- macOS: `~/Library/Logs/opc2ixon/opc2ixon.log`

Error dialogs and **Profiles → About…** have **Copy diagnostic info** (versions, OS, error and
the last log lines, ready to paste in an e-mail) and **Open log folder**.
Unexpected errors are shown in a dialog instead of closing the application.

## Secure connections

The app creates its own self-signed application certificate on first use. Siemens PLCs
usually reject unknown clients: use **Security → Export client certificate…** and add it to the
PLC's trusted certificates (TIA Portal → OPC UA server → Security → trusted clients), or enable
"automatically accept client certificates" on the PLC during commissioning.
Server certificates are confirmed once and stored in the app's `pki/trusted` folder.

## Where settings are stored

Profiles (`profiles/*.json`) and certificates (`pki/`) are in:

- Windows: `%LOCALAPPDATA%\opc2ixon\`
- macOS: `~/Library/Application Support/opc2ixon/`

Remembered passwords are in the macOS Keychain / Windows Credential Manager (service `opc2ixon`).
On first start, profiles, trusted server certificates and passwords of the previous name
(`IXON-OPC-Browser`) are copied once; the old folder is left untouched.

## Build the executables

Build on each operating system for that system (a Mac builds the Mac app, a Windows PC builds
the Windows exe). Requirements: Python 3.10–3.14 from python.org (64 bit) and internet access
for the packages. The scripts create a separate `.venv-build`, they do not touch `.venv`.

**macOS** (Terminal, in the project folder):

```bash
bash packaging/build_mac.sh
```
→ `dist/opc2ixon-<version>-macOS-arm64.zip` (contains `opc2ixon.app`, `NOTICE.txt`, `THIRD_PARTY_LICENSES.txt`).

**Windows** (double-click, or from a command prompt in the project folder):

```bat
packaging\build_windows.bat
```
→ `dist\opc2ixon-<version>-Windows.zip` (folder with `opc2ixon.exe`, `NOTICE.txt`, `THIRD_PARTY_LICENSES.txt`).

Quick check of a build against a server (no window, prints the number of variables):

```bash
"dist/opc2ixon.app/Contents/MacOS/opc2ixon" --selftest opc.tcp://localhost:4840
dist\opc2ixon\opc2ixon.exe --selftest opc.tcp://localhost:4840
```
(on Windows the result is written to the log, because the exe has no console).

The version number is in `opc2ixon/__init__.py` (`__version__`).

### First start on the customer's computer (apps are not signed)

- **Windows**: unzip, open the folder, run `opc2ixon.exe`. SmartScreen shows
  "Windows protected your PC" → **More info → Run anyway** (only the first time).
- **macOS**: unzip, move the app to Applications, double-click. macOS says it cannot verify the
  developer → **System Settings → Privacy & Security → Open Anyway** (only the first time).

License notices (`NOTICE.txt`, `THIRD_PARTY_LICENSES.txt`) are in the zip next to the application, and also inside it.

## Tests

```bash
pip install pytest
python -m pytest
```

## Project structure

```
run.py                      entry point
opc2ixon/
  profiles.py               profile model + JSON store + keychain password
  opc/client.py             endpoints probe, security, certificates, login
  opc/browser.py            batched recursive browse, type resolution
  opc/type_mapping.py       OPC UA → IXON types
  opc/naming.py             IXON identifier rules
  export/ixon_csv.py        CSV read / write / merge
  ui/                       PySide6 windows (profiles, browser, export)
  diagnostics.py            log file, error dialogs, diagnostic info
packaging/                  PyInstaller spec + build scripts (macOS / Windows)
tools/demo_server.py        test server
tests/                      unit + integration tests
```
