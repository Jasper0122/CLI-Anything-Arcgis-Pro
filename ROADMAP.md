# Roadmap

Where **CLI-Anything · ArcGIS Pro** is headed, and where you can help.

> **North star:** an AI agent should be able to take a task from *raw data → analysis → a finished, publication-ready map* end to end — with no human clicking in the GUI. We're most of the way on **analysis**; the open frontier is **cartographic authoring** (making the map look right) and **feature editing** (writing data back).

---

## How to read this

Each item lists the **command / MCP tool** to add, its rough signature, and the underlying API on **both** execution paths (see *Two API worlds* below). Items are tagged:

- 🟢 **good first issue** — small, self-contained, mentorship available
- 🔌 **MCP layer** — the stdlib `live-bridge/mcp_server.py`. **No ArcGIS license needed**, and the single best place to start — see [**Start here: the MCP layer**](#start-here-the-mcp-layer) below.
- 🐍 **no ArcGIS license needed** — stdlib MCP server / CLI wiring / tests / docs only
- 🪟 **needs a licensed ArcGIS Pro** — exercises real ArcPy (install Pro + a license, that's it)
- 🏗️ **needs the add-in dev environment** — a *live*-path item: building `ProSimpleMapExport` needs **Visual Studio + the ArcGIS Pro SDK for .NET**, on top of a Pro license. Heavier setup than a 🪟 ArcPy item.

If you want to contribute, open an issue (or comment on an existing one) before a large PR so we can align on the command shape.

> **No ArcGIS license? Start with the MCP layer (🔌🐍).** It's pure-stdlib Python, it's where the agent-native value lives, and almost every capability below needs an MCP tool exposed for it. You can build and test the whole MCP path against a **mock bridge** — no ArcGIS Pro required. Jump to [**Start here: the MCP layer**](#start-here-the-mcp-layer).

---

## Two API worlds (read this first)

The same GIS concept is reached through **two different APIs**, one per execution mode:

| | Headless CLI (`.aprx` on disk) | Live bridge (the open session) |
|---|---|---|
| API | **ArcPy** — `arcpy.mp`, `arcpy.da` | **ArcGIS Pro SDK for .NET** (C# add-in) |
| Threading | runs in-process | everything wrapped in `QueuedTask.Run` (CIM thread) |
| Editing | `arcpy.da` cursors inside an `Editor` session | **`EditOperation`** (gives undo/redo) |

**Key constraint:** an external process *cannot* obtain `ArcGISProject("CURRENT")` — that only works inside Pro's built-in Python window. So every **write** to the live session must go through the **.NET add-in** (`live-bridge/ProSimpleMapExport`). New capabilities are generally implemented twice, or deliberately scoped to one path.

**The CIM escape hatch.** Anything the high-level API doesn't expose (deep symbol/label/layout control) is reachable by dropping to the **CIM** (the low-level JSON definition):

- ArcPy: `lyr.getDefinition('V3')` → mutate → `lyr.setDefinition(cim)` (Pro 3.x = `'V3'`)
- .NET: `layer.GetDefinition()` → mutate `ArcGIS.Core.CIM` objects → `layer.SetDefinition()` (inside `QueuedTask`)

A generic `cim get` / `cim set` power-tool (see Phase 3) lets an agent reach anything we haven't wrapped yet.

---

## Shipped — v0.1.0

The analysis + export half is in place.

| Area | Surface |
|---|---|
| Environment | `info` |
| Project (read) | `project inspect`, `project layers` |
| Cartography (export) | `layout list`, `layout export`, `layout mapseries`, `batch export-layouts` |
| Data (read + field calc) | `data describe`, `data fields`, `data count`, `data query`, `data calc` |
| Geoprocessing | `gp` — runs **any** ArcToolbox tool |
| Map authoring (headless) | `map add-data`, `map symbology graduated`, `map symbology unique` — *new, [#4](https://github.com/Jasper0122/CLI-Anything-Arcgis-Pro/pull/4)* |
| Live MCP tools | `arcgis_ping`, `arcgis_query`, `arcgis_run_gp`, `arcgis_zoom_to`, `arcgis_export_layout` |
| MCP test harness | mock bridge + `tools/list`/`tools/call` tests (license-free) — *new, [#5](https://github.com/Jasper0122/CLI-Anything-Arcgis-Pro/pull/5)* |
| MCP tool (Python shipped, .NET pending) | `arcgis_symbology` — forwarding mock-tested; live `DoSymbology` handler awaits a Pro-SDK build — *[#5](https://github.com/Jasper0122/CLI-Anything-Arcgis-Pro/pull/5)* |

What's missing: the agent can run a buffer, but it can't yet **add the result to a map, symbolize it, compose a layout, or edit features** — that's the rest of this roadmap.

> **Convention added after v0.1.0 — destructive ops are deny-by-default.** `gp` and `data calc` now route through `cli_anything_arcgis_pro/_safety.py`: any tool that deletes/truncates is blocked unless the caller opts in (`--allow-delete` / MCP `allow_delete=true` / `ARCGIS_CLI_ALLOW_DELETE=1`). See [#1](https://github.com/Jasper0122/CLI-Anything-Arcgis-Pro/pull/1). **Any new destructive command or MCP tool below (delete, delete-field, truncate, edit-with-delete) must call the same guard** — don't reintroduce the footgun #1 just closed. The `_safety` helpers are pure/backend-free, so cover them in `tests/test_core.py`.

---

## Start here: the MCP layer

**If you have no ArcGIS license, this is your track.** `live-bridge/mcp_server.py` is pure-stdlib Python — it's the agent-facing surface that turns every capability below into something an LLM can call. It needs the most help and the least setup.

```
Agent ──JSON-RPC──► mcp_server.py ──HTTP──► in-Pro .NET bridge ──► live project
        (🔌 here, no license)         (🏗️ add-in, gated)
```

The key unlock: **the MCP half of a feature is separable from the .NET half.** Defining a tool, its `inputSchema`, validating args, forwarding the right command to the bridge, shaping the response — all of that is pure Python you can build and test **against a mock bridge**, without ArcGIS Pro ever running. The `🏗️` .NET executor can land later.

Concrete MCP work, roughly in dependency order:

- ✅ 🔌 🐍 **Mock bridge for tests** — *shipped in [#5](https://github.com/Jasper0122/CLI-Anything-Arcgis-Pro/pull/5)* (`tests/test_mcp_server.py`): a loopback HTTP server stands in for the .NET bridge, records what the MCP server forwarded, and returns canned responses. The whole MCP path is now testable with zero ArcGIS install — **build new MCP tools on this harness.**
- ✅ 🔌 🐍 **`tools/list` + `tools/call` tests** — *shipped in [#5]* — assert tools come back with valid schemas and that calls forward the right command. Extend these as you add tools.
- 🔌 🐍 **Protocol completeness** — a proper `initialize` handshake with advertised `capabilities`, correct JSON-RPC error codes (`-32601` unknown method, `-32602` bad params), and graceful bridge-down handling instead of a raw traceback.
- 🔌 🐍 **Richer tool schemas** — tighten each tool's `inputSchema` (required fields, enums, descriptions) so agents call them correctly the first time. Cheap, high-leverage for agent reliability.
- 🔌 🐍 **Expose new capabilities as tools** — as Phase 1/2 land, add the matching MCP tool: `arcgis_add_data`, `arcgis_set_layer`, ~~`arcgis_symbology`~~ ✅, `arcgis_select`, `arcgis_edit`. The Python tool definition + schema + mock-bridge test is 🐍 license-free; only the `🏗️` .NET command behind it needs the add-in. **You can ship and test the MCP side independently** — `arcgis_symbology` ([#5](https://github.com/Jasper0122/CLI-Anything-Arcgis-Pro/pull/5)) is the worked example: Python tool shipped + mock-tested, with the `🏗️` `DoSymbology` handler written but pending a Pro-SDK build.
- 🔌 🐍 **Bridge auth** ([#3](https://github.com/Jasper0122/CLI-Anything-Arcgis-Pro/issues/3)) — the bridge's `127.0.0.1` listener has no auth/origin check. Add a shared-secret token the MCP server sends and the bridge verifies.

Every `**MCP:**` line in the phases below is one of these — pick a capability, build its tool + schema + mock-tested forwarding, and it's a complete, mergeable contribution even before the live executor exists.

---

## Phase 1 — Cartographic authoring (the differentiator) → v0.2.0

This is where ArcGIS Pro beats QGIS, and it's our biggest gap. Completing Phase 1 closes the core narrative loop:

> `gp` (analyze) → `map add-data` (add result) → `map symbology graduated` (auto-classify colors) → `layout export` (finished map) — **zero human clicks.**

### 1.1 Layer management 🪟
Add/remove/configure layers in a map. ✅ **Partially shipped (headless):** `map add-data`. Remaining: `remove-layer`, `set-visible`, `def-query`, `move`, and the live .NET path + MCP tools.

- **Commands:** `map add-data <path> [--map] [--name]`, `map remove-layer <name>`, `map set-visible <name> <bool>`, `map def-query <name> <sql>`
- Headless: `Map.addDataFromPath()`, `Map.addLayer()`, `Map.removeLayer()`, `lyr.visible`, `lyr.definitionQuery`, `Map.moveLayer()`
- Live (.NET): `LayerFactory.Instance.CreateLayer(uri, map)`, `map.RemoveLayer()`, `layer.SetVisibility()`, `layer.SetDefinitionQuery(sql)`
- **MCP:** `arcgis_add_data`, `arcgis_set_layer`
- **Acceptance:** after `gp` produces an output, one command makes it a visible layer in the active map; verified in both headless and live paths.

### 1.2 Symbology 🪟 — *highest-value single item*
Apply renderers so the map actually communicates. ✅ **Partially shipped (headless):** `map symbology graduated` / `unique`. Remaining: `simple`, `apply-lyrx`, the live .NET path, and the `arcgis_symbology` MCP tool.

> 🩹 **Two gotchas baked into the shipped code** (save the next person the afternoon): (1) never cache `sym.renderer` in a variable — assign through `sym.renderer.<prop>` each time, a cached handle goes stale after the first set; (2) `updateRenderer` needs a **valid data source with the field present** (it reads the data to classify) — a broken source silently leaves a `SimpleRenderer`, so verify `lyr.symbology.renderer.type` actually switched and error out if not.

- **Commands:** `map symbology graduated <layer> --field --classes --ramp`, `map symbology unique <layer> --field`, `map symbology simple <layer> --color`, `map apply-lyrx <layer> <lyrx>`
- Headless (standard 4-step): `sym = lyr.symbology` → `sym.updateRenderer('GraduatedColorsRenderer')` → set `sym.renderer.classificationField`, `breakCount`, `colorRamp` → `lyr.symbology = sym`. For `unique`: `sym.updateRenderer('UniqueValueRenderer')` then `sym.renderer.fields = [...]`. Template path: `arcpy.management.ApplySymbologyFromLayer`.
  - ⚠️ **`colorRamp` gotcha:** you can't assign a ramp *name string* — fetch the object first: `sym.renderer.colorRamp = aprx.listColorRamps('Viridis')[0]`. Map the `--ramp` arg to a `listColorRamps()` lookup.
- Supported renderers: `SimpleRenderer`, `GraduatedColorsRenderer`, `GraduatedSymbolsRenderer`, `UnclassedColorsRenderer`, `UniqueValueRenderer`.
- Live (.NET): **prefer the high-level path** — `featureLayer.CreateRenderer(new GraduatedColorsRendererDefinition(field, classes, rampStyle))` (or `UniqueValueRendererDefinition`) → `featureLayer.SetRenderer(renderer)`, all inside `QueuedTask`. Only drop to the CIM route (`GetDefinition()` → swap `CIMFeatureLayer.Renderer` → `SetDefinition()`) for control the definition classes don't expose. Don't start with raw CIM surgery.
- **MCP:** `arcgis_symbology`
- **Acceptance:** a quantitative field renders as graduated colors; a categorical field renders as unique values; both visible in the live session and in an exported PDF.

### 1.3 Labeling 🪟
- **Commands:** `map labels on <layer> --field` / `map labels off <layer>` / `map labels expression <layer> <expr>`
- Headless: `lyr.showLabels`, `lyr.listLabelClasses()`, label class `.expression`; deeper styling via CIM `CIMLabelClass`.
- Live (.NET): CIM `CIMFeatureLayer.LabelClasses` + `LabelVisibility`.
- **Acceptance:** labels toggle on with a chosen field and survive export.

### 1.4 Layout authoring (build a map, don't just export one) 🪟
- **Commands:** `layout create --name --width --height --units`, `layout add-element legend|scalebar|northarrow|text <layout> [--map-frame] [--text]`
- Headless: `aprx.createLayout(w, h, units)`, `layout.createMapFrame(geom, map)`, `layout.createMapSurroundElement(geom, 'LEGEND'|'SCALE_BAR'|'NORTH_ARROW', mapframe)`, `layout.createTextElement(...)`
- Live (.NET): `ElementFactory.Instance.CreateMapFrameElement / CreateLegendElement / CreateScaleBarElement / CreateNorthArrowElement`, text via `CreateTextGraphicElement`
- **Acceptance:** an agent builds a layout from scratch (map frame + legend + scale bar + title) and exports it to PDF.

---

## Phase 2 — Feature editing & selection (the write half) → v0.3.0

"Operate ArcGIS" means CRUD, not just read. Today we have `data query` (read) + `data calc` (field calc) only.

> ⚠️ **This whole phase is destructive — wire it into `_safety`.** `data delete`, the delete half of `data update`, `delete-field`, and any delete-capable MCP `arcgis_edit` must go through the deny-by-default guard (`_safety.guard_tool` / `guard_expr`, opt-in via `--allow-delete` / `allow_delete`). This is the convention from [#1](https://github.com/Jasper0122/CLI-Anything-Arcgis-Pro/pull/1); a CRUD command that can wipe a feature class silently is exactly what it prevents.

### 2.1 Feature CRUD 🪟
- **Commands:** `data insert <fc> --json`, `data update <fc> --where --set`, `data delete <fc> --where`
- Headless: `arcpy.da.InsertCursor` (add), `arcpy.da.UpdateCursor` (update + delete), **wrapped in an `arcpy.da.Editor(workspace)` edit session** to respect locks/versioned data; geometry via `SHAPE@` / `SHAPE@XY` tokens.
- Live (.NET) — **must** use this path on open data: `EditOperation` → `op.Create(layer, attrs)` / `op.Modify()` / `op.Delete()` / `op.Execute()` inside `QueuedTask`. ⚠️ Never open a raw ArcPy cursor on data Pro has open — it locks/conflicts.
- **MCP:** `arcgis_edit`
- **Acceptance:** insert/update/delete a feature in the live session with working undo; the same operations work headless against a file GDB.

### 2.2 Schema management 🪟 🟢
- **Commands:** `data create-fc`, `data add-field`, `data delete-field`
- Headless: `arcpy.management.CreateFeatureclass`, `AddField`, `DeleteField`
- **Acceptance:** create an empty feature class with defined fields, then insert into it via 2.1.

### 2.3 Selection 🪟
- **Commands / MCP:** `arcgis_select <layer> --where | --intersects`, `arcgis_clear_selection`
- Headless: `arcpy.management.SelectLayerByAttribute`, `SelectLayerByLocation`
- Live (.NET): `layer.Select(QueryFilter)`, `MapView.Active.SelectFeatures(geom)`, `map.ClearSelection()`
- **Note:** `arcgis_zoom_to` currently does a where-select as a side effect — split that into a first-class selection capability.

---

## Phase 3 — Power tools & polish → v0.4.0

### 3.1 Generic CIM access 🪟
- **Commands / MCP:** `cim get <layer|layout>`, `cim set <layer|layout> --json` — the universal escape hatch for anything unwrapped.

### 3.2 Navigation (live) 🪟
- `arcgis_pan`, `arcgis_set_scale`, `arcgis_zoom_bookmark`, switch active map/view.
- Live (.NET): `MapView.Active` — `PanTo`, `camera.Scale`, `ZoomToBookmark`.

### 3.3 Project/document management 🪟
- `project save`, `project save-copy`, `project new-map`, import `.mapx` / `.lyrx`.
- Headless: `aprx.save()`, `aprx.saveACopy()`, `aprx.createMap()`. Live: `Project.Current.SaveAsync()`, `MapFactory.Instance.CreateMap`.

---

## Project health (parallel track, mostly license-free)

These don't need ArcGIS Pro and are great entry points:

- 🐍 🟢 **CI** — GitHub Actions running `tests/test_core.py` on each push, plus a tests badge. The suite is already backend-free and **grew in [#1](https://github.com/Jasper0122/CLI-Anything-Arcgis-Pro/pull/1)** (the `_safety` guard now has coverage) — it just isn't run automatically yet. **Highest-value health item.**
- 🔌 🐍 🟢 **MCP server tests** — see [Start here: the MCP layer](#start-here-the-mcp-layer); the mock bridge + `tools/list` test live there.
- 🐍 🟢 **Docs** — quickstart recipes, a worked "data → map" example, expand `SKILL.md`.
- 🐍 🟢 **Mock-based CLI tests** — verify command wiring without a Pro license (same mock-bridge idea, applied to the CLI).
- 🏗️ **Guard the .NET executor** ([#2](https://github.com/Jasper0122/CLI-Anything-Arcgis-Pro/issues/2)) — mirror the `_safety` delete/truncate guard inside `BridgeServer.cs`, so direct bridge calls can't bypass it.

---

## Later / long-tail (not scheduled)

Raster & mosaic datasets · 3D scenes · publishing to ArcGIS Online / web maps · Report authoring · ModelBuilder/Task integration. Open an issue if you need one of these and we'll prioritize by demand.

---

## Contributing

Heads-up: ArcGIS Pro is Esri's commercial, **Windows-only** desktop app, so 🪟 items need a licensed install and 🏗️ items also need the .NET add-in dev environment (Visual Studio + ArcGIS Pro SDK). But the 🔌 🐍 items — the **MCP layer**, tests, and docs — need **none of that**, and they're where the agent-native value lives. **If you don't have ArcGIS Pro, go to [Start here: the MCP layer](#start-here-the-mcp-layer)** — build the mock bridge or a tool's schema and you've made a real, testable contribution without ever installing Pro. "Just try it and file an issue where it breaks" is genuinely valuable too. See the **Contributing** section of the [README](README.md). First-timers welcome on anything tagged 🟢.
