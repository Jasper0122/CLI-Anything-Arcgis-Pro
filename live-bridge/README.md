# Live bridge + MCP — operate the OPEN ArcGIS Pro

This is the part the headless CLI can't do: drive the **running** ArcGIS Pro
session so an agent can act on the open project while the user watches.

## Why it's needed

External ArcPy can't attach to a live ArcGIS Pro (Esri's `"CURRENT"` only
resolves inside Pro's own process). So the bridge runs **inside** Pro as a .NET
add-in, hosting a tiny loopback HTTP server. An external MCP server forwards an
agent's tool calls to it; the add-in executes them on the live project via
`QueuedTask.Run` and returns structured JSON.

```
Agent ──MCP/stdio──► mcp_server.py ──HTTP 127.0.0.1:5005─► add-in (in Pro) ─► live project
```

## Components

- **`ProSimpleMapExport/`** — the ArcGIS Pro add-in (.NET 8 / Pro SDK):
  - `BridgeServer.cs` — loopback server + command handlers (`ping`, `export_layout`, `zoom_to`, `query`, `run_gp`).
  - `Module1.cs` — autoloads at startup and starts the bridge.
  - `ExportLayoutButton.cs` — a ribbon button (also exports the active layout).
  - `Config.daml` — add-in manifest (ribbon tab + module).
- **`mcp_server.py`** — a dependency-free (stdlib-only) MCP stdio server that
  forwards tool calls to the bridge.

## Build the add-in

Needs the **.NET 8 SDK** (Visual Studio optional). From `ProSimpleMapExport/`:

```bat
dotnet build -c Release
```

This compiles and packages `bin\Release\ProSimpleMapExport.esriAddinX`. The
`PackageAddIn` MSBuild target produces a valid package: `Config.daml` at the zip
root and the assembly under `Install\`.

> Gotcha worth knowing: in a `.esriAddinX`, the DLL **must** live in an
> `Install\` subfolder. If it sits at the root, Pro shows the ribbon UI (from
> Config.daml) but never loads the assembly, so the module never initializes.

## Install into ArcGIS Pro

Close Pro, then copy the package to the well-known add-in folder and reopen Pro:

```
%USERPROFILE%\Documents\ArcGIS\AddIns\ArcGISPro\ProSimpleMapExport.esriAddinX
```

(Or double-click the `.esriAddinX` to use Esri's Add-In Manager.) On startup the
module autoloads and the bridge listens on `127.0.0.1:5005`.

## Register the MCP server with Claude Code

For Claude Code:

```bat
claude mcp add arcgis-pro --scope user -- python C:\path\to\live-bridge\mcp_server.py
```

## Register the MCP server with Codex

Codex can run the same stdio MCP server. From a terminal, register it with:

```bat
codex mcp add arcgis-pro -- python C:\path\to\CLI-Anything-Arcgis-Pro\live-bridge\mcp_server.py
```

Or add it to your Codex `config.toml`:

```toml
[mcp_servers.arcgis-pro]
command = "python"
args = ["C:\\path\\to\\CLI-Anything-Arcgis-Pro\\live-bridge\\mcp_server.py"]
startup_timeout_sec = 10
tool_timeout_sec = 180
```

After registration, restart Codex or open a new Codex session so the MCP server
is loaded. Start with `arcgis_ping`; it verifies that ArcGIS Pro is open, the
add-in bridge is reachable, and reports the available maps and layouts. For
`arcgis_run_gp`, use full Windows dataset paths for inputs and outputs because
short layer names are unreliable in background geoprocessing. Destructive GP
tools (`Delete*` / `Truncate*`) are blocked unless you intentionally pass
`allow_delete=true`.

`mcp_server.py` is stdlib-only, so any Python 3 works. It forces UTF-8 stdio
(important on non-UTF-8 locales) and tolerates a leading BOM.

## MCP tools

| Tool | Action on the live project |
|---|---|
| `arcgis_ping` | Project name, maps, layouts, active map/layout. |
| `arcgis_export_layout` | Export a layout to a PDF (`out`, `layout?`, `dpi?`). |
| `arcgis_zoom_to` | Zoom the active map view to `layer` (optional `where` → zoom to selection). |
| `arcgis_query` | Query `layer` attributes (`where?`, `map?`, `limit?`) → structured rows. |
| `arcgis_run_gp` | Run any GP tool: `tool` (e.g. `analysis.Buffer`) + `params` (positional). Outputs added to the live map. |

### `run_gp` notes (learned the hard way)

- Use the **non-modal** `ExecuteToolAsync` overload (CancellationToken +
  GPToolExecuteEventHandler). The `CancelableProgressor` overload routes through
  a modal progress dialog and throws `NullReferenceException` when called headless.
- Pass **full dataset paths** for inputs/outputs (`…\x.gdb\fc`); layer-name
  resolution is unreliable for background GP and fails silently.

## Adding a command

1. `BridgeServer.cs`: add a `case` in `Dispatch` + a `DoXxx(JsonElement)` handler
   (run ArcGIS work inside `QueuedTask.Run`).
2. `mcp_server.py`: add a tool to `TOOLS` and an entry in `command_map`.
3. Bump `version` in `Config.daml` (Pro caches assemblies by id/version),
   rebuild, redeploy, restart Pro. Re-registering the MCP server isn't needed if
   the launch command is unchanged — just restart the MCP client.
