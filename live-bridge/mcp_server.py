"""MCP server bridging Claude Code to the LIVE ArcGIS Pro session.

Architecture:
    Claude Code  --stdio JSON-RPC-->  this server  --HTTP-->  ProSimpleMapExport
    add-in (inside ArcGIS Pro, 127.0.0.1:5005)  -->  live project (QueuedTask).

Zero third-party deps: implements the MCP stdio protocol by hand using only the
standard library, so it runs on any Python 3 without `pip install`.
"""

import os
import sys
import json
import urllib.request
import urllib.error

# Where the in-Pro add-in bridge listens. Override with ARCGIS_BRIDGE_URL when the
# server and ArcGIS Pro are on different hosts (e.g. running this in a container).
BRIDGE_URL = os.environ.get("ARCGIS_BRIDGE_URL", "http://127.0.0.1:5005/")

# --- Deny-by-default deletion guard -----------------------------------------
# Block any geoprocessing tool whose name looks destructive (Delete*/Truncate*)
# unless the caller explicitly opts in via an `allow_delete` argument or the
# ARCGIS_CLI_ALLOW_DELETE environment variable. Stops run_gp from deleting
# shapefiles / feature classes / geodatabase contents by default.
_DESTRUCTIVE_TOOL_TOKENS = ("delete", "truncate")
_TRUTHY = {"1", "true", "yes", "on", "y", "t"}


def _deletion_allowed(args):
    if str((args or {}).get("allow_delete", "")).strip().lower() in _TRUTHY:
        return True
    return os.environ.get("ARCGIS_CLI_ALLOW_DELETE", "").strip().lower() in _TRUTHY


def _is_destructive_tool(tool):
    t = (tool or "").lower()
    return any(tok in t for tok in _DESTRUCTIVE_TOOL_TOKENS)


def log(msg):
    print(f"[arcgis-mcp] {msg}", file=sys.stderr, flush=True)


def call_bridge(payload, timeout=180):
    """POST a command to the in-Pro bridge and return its parsed JSON."""
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        BRIDGE_URL, data=data, headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.URLError as e:
        return {
            "ok": False,
            "error": (
                f"无法连接 ArcGIS Pro 桥 ({BRIDGE_URL}): {e}. "
                "请确认 ArcGIS Pro 正在运行且已加载 ProSimpleMapExport add-in。"
            ),
        }
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"桥调用失败: {e}"}


TOOLS = [
    {
        "name": "arcgis_ping",
        "description": (
            "读取当前打开的 ArcGIS Pro 工程状态：工程名、所有地图、所有布局、"
            "以及当前活动的地图/布局。发其它命令前先用它了解打开的是什么工程。"
        ),
        "inputSchema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "arcgis_export_layout",
        "description": (
            "把活着的 ArcGIS Pro 工程里的某个布局导出为 PDF。导出在用户正开着的 "
            "Pro 实例内执行（用户能看到），返回输出路径与文件大小。"
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "out": {
                    "type": "string",
                    "description": r"输出 PDF 的绝对路径，例如 C:\temp\map.pdf",
                },
                "layout": {
                    "type": "string",
                    "description": "布局名（可选；缺省用活动布局，否则用第一个布局）。",
                },
                "dpi": {"type": "integer", "description": "分辨率 DPI（默认 300）。"},
            },
            "required": ["out"],
        },
    },
    {
        "name": "arcgis_zoom_to",
        "description": (
            "把活动地图视图缩放到某个要素图层（用户能在窗口里看到地图动）。"
            "可选 where 条件：会先选中匹配要素再缩放到选集。需要 Pro 当前处于地图视图（非布局）。"
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "layer": {"type": "string", "description": "要缩放到的要素图层名。"},
                "where": {
                    "type": "string",
                    "description": "可选 SQL 条件，如 \"POP > 1000\"；给了就缩放到选中要素。",
                },
            },
            "required": ["layer"],
        },
    },
    {
        "name": "arcgis_query",
        "description": (
            "查询活工程里某要素图层的属性，返回结构化的行（不含几何）。可按 where 过滤、限制行数。"
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "layer": {"type": "string", "description": "要素图层名。"},
                "where": {"type": "string", "description": "可选 SQL where 条件。"},
                "map": {"type": "string", "description": "可选地图名（缺省用活动/第一个地图）。"},
                "limit": {"type": "integer", "description": "最多返回多少行（默认 50，0=不限）。"},
            },
            "required": ["layer"],
        },
    },
    {
        "name": "arcgis_run_gp",
        "description": (
            "在活工程上运行任意 ArcGIS 地理处理工具（整个 ArcToolbox：分析/管理/转换/栅格…）。"
            "输出图层会自动加到当前地图（用户能看到）。tool 用点号写法如 'analysis.Buffer'、"
            "'management.Clip'；params 是按工具签名顺序排列的位置参数字符串数组。"
            "例：tool='analysis.Buffer'，params=['roads','roads_buf','100 Meters']。"
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "tool": {
                    "type": "string",
                    "description": "工具名，点号写法，如 analysis.Buffer / management.Dissolve / sa.Slope。",
                },
                "params": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "按工具参数顺序的位置参数（字符串）。"
                        "输入/输出强烈建议用数据集全路径（如 C:\\...\\x.gdb\\fc），"
                        "图层名在后台 GP 里解析不可靠。距离等参数如 '500 Meters'。"
                    ),
                },
                "allow_delete": {
                    "type": "boolean",
                    "description": (
                        "Safety opt-in. Destructive tools (Delete*/Truncate*) are BLOCKED by "
                        "default to protect shapefiles, feature classes and geodatabases. Set "
                        "true ONLY when you intend to delete or truncate data."
                    ),
                },
            },
            "required": ["tool", "params"],
        },
    },
    {
        "name": "arcgis_symbology",
        "description": (
            "给活工程里某要素图层套用渲染器，让地图会说话——用户能在 Pro 窗口里立刻看到变色。"
            "renderer='graduated' 用数值字段做分级配色（专题图主力）；'unique' 用分类字段每个值一种颜色。"
            "例：layer='tracts', renderer='graduated', field='MEDINCOME', classes=5, ramp='Viridis'。"
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "layer": {"type": "string", "description": "要渲染的要素图层名。"},
                "renderer": {
                    "type": "string",
                    "enum": ["graduated", "unique"],
                    "description": "graduated=数值字段分级色；unique=分类字段唯一值色。",
                },
                "field": {
                    "type": "string",
                    "description": "用于渲染的字段。graduated 需数值字段；unique 用分类字段。",
                },
                "classes": {
                    "type": "integer",
                    "description": "graduated 的分级数（默认 5；unique 忽略）。",
                },
                "method": {
                    "type": "string",
                    "enum": [
                        "NaturalBreaks",
                        "EqualInterval",
                        "Quantile",
                        "GeometricInterval",
                        "StandardDeviation",
                    ],
                    "description": "graduated 的分类方法（默认 NaturalBreaks；unique 忽略）。",
                },
                "ramp": {"type": "string", "description": "色带名，如 'Viridis'（可选）。"},
                "map": {"type": "string", "description": "可选地图名（缺省用活动/第一个地图）。"},
            },
            "required": ["layer", "renderer", "field"],
        },
    },
]


def handle(req):
    method = req.get("method")
    rid = req.get("id")

    if method == "initialize":
        client_ver = (req.get("params") or {}).get("protocolVersion", "2024-11-05")
        return ok(rid, {
            "protocolVersion": client_ver,
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "arcgis-pro-bridge", "version": "1.0.0"},
        })

    if method in ("notifications/initialized", "initialized"):
        return None  # notification, no response

    if method == "ping":
        return ok(rid, {})

    if method == "tools/list":
        return ok(rid, {"tools": TOOLS})

    if method == "tools/call":
        params = req.get("params") or {}
        name = params.get("name")
        args = params.get("arguments") or {}
        command_map = {
            "arcgis_ping": "ping",
            "arcgis_export_layout": "export_layout",
            "arcgis_zoom_to": "zoom_to",
            "arcgis_query": "query",
            "arcgis_run_gp": "run_gp",
            "arcgis_symbology": "symbology",
        }
        cmd = command_map.get(name)
        if cmd is None:
            return err(rid, -32601, f"unknown tool: {name}")
        # Deny-by-default: block destructive geoprocessing unless explicitly allowed.
        if cmd == "run_gp" and _is_destructive_tool(args.get("tool")) and not _deletion_allowed(args):
            blocked = (
                f"Refused to run destructive tool {args.get('tool')!r}: it deletes or truncates "
                "data (shapefiles, feature classes, geodatabase contents) and is blocked by "
                "default. To proceed intentionally, set allow_delete=true in the tool arguments "
                "or set ARCGIS_CLI_ALLOW_DELETE=1 in the server environment."
            )
            return ok(rid, {"content": [{"type": "text", "text": blocked}], "isError": True})
        payload = {"command": cmd}
        payload.update(args)
        result = call_bridge(payload)
        text = json.dumps(result, ensure_ascii=False, indent=2)
        return ok(rid, {
            "content": [{"type": "text", "text": text}],
            "isError": not result.get("ok", False),
        })

    if rid is not None:
        return err(rid, -32601, f"method not found: {method}")
    return None


def ok(rid, result):
    return {"jsonrpc": "2.0", "id": rid, "result": result}


def err(rid, code, message):
    return {"jsonrpc": "2.0", "id": rid, "error": {"code": code, "message": message}}


# BOM / zero-width codepoints some pipes prepend to the first line.
_BOM_CODEPOINTS = (0xFEFF, 0xFFFE, 0x200B)


def main():
    # Claude Code speaks UTF-8 over stdio. On a non-UTF-8 locale (e.g. Chinese GBK)
    # Python would otherwise mis-decode the stream — force UTF-8 both ways.
    try:
        sys.stdin.reconfigure(encoding="utf-8")
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    log(f"started, bridging to {BRIDGE_URL}")
    for raw in sys.stdin:
        line = raw.strip()
        while line and ord(line[0]) in _BOM_CODEPOINTS:
            line = line[1:]
        if not line:
            continue
        try:
            req = json.loads(line)
        except Exception as e:  # noqa: BLE001
            log(f"bad json: {e}")
            continue
        try:
            resp = handle(req)
        except Exception as e:  # noqa: BLE001
            rid = req.get("id")
            resp = err(rid, -32603, str(e)) if rid is not None else None
        if resp is not None:
            sys.stdout.write(json.dumps(resp) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()
