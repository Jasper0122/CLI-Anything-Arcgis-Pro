"""Backend-free tests for the live-bridge MCP server.

No ArcGIS license, no arcpy, no running ArcGIS Pro: the stdlib MCP server
(`live-bridge/mcp_server.py`) is exercised against a **mock bridge** — a tiny
loopback HTTP server that stands in for the in-Pro .NET add-in, records what the
MCP server forwarded, and returns a canned response. This lets the whole MCP
path (`tools/list`, `tools/call`, forwarding, the delete guard) be tested
anywhere, and is the harness new MCP contributions should build on.
"""

import importlib.util
import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
_MCP_PATH = os.path.join(_HERE, "..", "live-bridge", "mcp_server.py")


def _load_mcp():
    spec = importlib.util.spec_from_file_location("mcp_server", _MCP_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


mcp = _load_mcp()


class _Recorder:
    """Captures the last payload the MCP server POSTed to the (mock) bridge."""

    def __init__(self):
        self.last = None
        self.response = {"ok": True, "data": {"stub": True}}


@pytest.fixture
def mock_bridge(monkeypatch):
    rec = _Recorder()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):  # silence the default stderr logging
            pass

        def do_POST(self):
            n = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(n).decode("utf-8") if n else ""
            rec.last = json.loads(body) if body else {}
            payload = json.dumps(rec.response).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

    srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    monkeypatch.setattr(mcp, "BRIDGE_URL", f"http://127.0.0.1:{srv.server_address[1]}/")
    try:
        yield rec
    finally:
        srv.shutdown()


def _call(name, arguments):
    return mcp.handle(
        {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
         "params": {"name": name, "arguments": arguments}}
    )


# --- protocol basics --------------------------------------------------------

def test_initialize_handshake():
    resp = mcp.handle(
        {"jsonrpc": "2.0", "id": 0, "method": "initialize",
         "params": {"protocolVersion": "2024-11-05"}}
    )
    assert resp["result"]["capabilities"]["tools"] == {}
    assert resp["result"]["serverInfo"]["name"] == "arcgis-pro-bridge"


def test_tools_list_advertises_symbology_with_valid_schema():
    resp = mcp.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    tools = {t["name"]: t for t in resp["result"]["tools"]}
    assert "arcgis_symbology" in tools
    schema = tools["arcgis_symbology"]["inputSchema"]
    assert schema["required"] == ["layer", "renderer", "field"]
    assert set(schema["properties"]["renderer"]["enum"]) == {"graduated", "unique"}


def test_unknown_tool_returns_method_not_found():
    resp = _call("arcgis_bogus", {})
    assert resp["error"]["code"] == -32601


# --- arcgis_symbology forwarding --------------------------------------------

def test_symbology_forwards_full_payload_to_bridge(mock_bridge):
    mock_bridge.response = {
        "ok": True,
        "data": {"layer": "tracts", "renderer": "GraduatedColorsRenderer",
                 "field": "POP", "classes": 4},
    }
    resp = _call("arcgis_symbology",
                 {"layer": "tracts", "renderer": "graduated", "field": "POP",
                  "classes": 4, "ramp": "Viridis"})

    # the MCP server translated the tool into the bridge's `symbology` command…
    assert mock_bridge.last["command"] == "symbology"
    assert mock_bridge.last["layer"] == "tracts"
    assert mock_bridge.last["renderer"] == "graduated"
    assert mock_bridge.last["field"] == "POP"
    assert mock_bridge.last["classes"] == 4
    assert mock_bridge.last["ramp"] == "Viridis"

    # …and shaped the bridge's reply as a non-error tool result.
    assert resp["result"]["isError"] is False
    assert "GraduatedColorsRenderer" in resp["result"]["content"][0]["text"]


def test_symbology_bridge_error_surfaces_as_iserror(mock_bridge):
    mock_bridge.response = {"ok": False, "error": "layer not found: tracts"}
    resp = _call("arcgis_symbology",
                 {"layer": "tracts", "renderer": "unique", "field": "CAT"})
    assert mock_bridge.last["command"] == "symbology"
    assert resp["result"]["isError"] is True
    assert "layer not found" in resp["result"]["content"][0]["text"]


# --- the delete guard still holds on the MCP path ---------------------------

def test_destructive_run_gp_blocked_before_reaching_bridge(mock_bridge):
    resp = _call("arcgis_run_gp", {"tool": "management.Delete", "params": ["x"]})
    assert resp["result"]["isError"] is True
    # crucial: it never forwarded the destructive call to the bridge
    assert mock_bridge.last is None


def test_benign_run_gp_does_reach_bridge(mock_bridge):
    _call("arcgis_run_gp", {"tool": "analysis.Buffer", "params": ["a", "b", "100 Meters"]})
    assert mock_bridge.last["command"] == "run_gp"
    assert mock_bridge.last["tool"] == "analysis.Buffer"
