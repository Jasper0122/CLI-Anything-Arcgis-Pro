"""Core tests — pass WITHOUT an ArcGIS Pro / ArcPy backend.

These exercise the CLI wiring (command tree, help, option parsing) and the
deletion guard in ``_safety`` (pure Python, no backend). ArcPy is imported lazily
inside command bodies, so loading the modules needs no backend.
"""

import pytest
from click.testing import CliRunner

from cli_anything_arcgis_pro.__main__ import cli
from cli_anything_arcgis_pro._safety import DeletionBlocked, guard_expr, guard_tool

EXPECTED_COMMANDS = ["info", "project", "layout", "data", "gp", "batch"]


def test_root_help():
    result = CliRunner().invoke(cli, ["--help"])
    assert result.exit_code == 0
    assert "Agent-friendly CLI over ArcGIS Pro" in result.output


def test_all_subcommands_present():
    result = CliRunner().invoke(cli, ["--help"])
    for cmd in EXPECTED_COMMANDS:
        assert cmd in result.output, f"missing command: {cmd}"


def test_json_is_group_level_flag():
    # --json belongs to the group, before the subcommand
    result = CliRunner().invoke(cli, ["--help"])
    assert "--json" in result.output


def test_layout_group_help():
    result = CliRunner().invoke(cli, ["layout", "--help"])
    assert result.exit_code == 0
    for sub in ["list", "export", "mapseries"]:
        assert sub in result.output


def test_gp_help_mentions_kw():
    result = CliRunner().invoke(cli, ["gp", "--help"])
    assert result.exit_code == 0
    assert "--kw" in result.output


def test_version():
    result = CliRunner().invoke(cli, ["--version"])
    assert result.exit_code == 0


# ---------------------------------------------------------------------------
# Deletion guard (cli_anything_arcgis_pro._safety) — no ArcPy backend needed
# ---------------------------------------------------------------------------

# --- tool-name guard (the solid half: substring on the tool identifier) -----

def test_guard_tool_blocks_delete_by_default():
    with pytest.raises(DeletionBlocked):
        guard_tool("management.Delete")


def test_guard_tool_blocks_legacy_and_variant_spellings():
    for tool in ("Delete_management", "management.DeleteFeatures", "management.TruncateTable"):
        with pytest.raises(DeletionBlocked):
            guard_tool(tool)


def test_guard_tool_allows_with_explicit_opt_in():
    guard_tool("management.Delete", allow_delete=True)  # must not raise


def test_guard_tool_allows_benign_tool():
    guard_tool("analysis.Buffer")  # must not raise


# --- calc expression guard (footgun-guard: call-style match) ----------------

def test_guard_expr_blocks_destructive_call():
    with pytest.raises(DeletionBlocked):
        guard_expr("arcpy.management.Delete('x')")


def test_guard_expr_blocks_rmtree_and_unlink():
    for expr in ("shutil.rmtree('x')", "os.unlink('x')"):
        with pytest.raises(DeletionBlocked):
            guard_expr(expr)


def test_guard_expr_allows_deleted_flag_field_name():
    # The false positive the maintainer flagged: a field NAME containing "delete",
    # used in a plain copy, must NOT be blocked (it is not a call).
    guard_expr("!DELETED_FLAG!")  # must not raise


def test_guard_expr_allows_benign_expression():
    guard_expr("!POP! * 2")  # must not raise


def test_guard_expr_allows_with_explicit_opt_in():
    guard_expr("arcpy.management.Delete('x')", allow_delete=True)  # must not raise


def test_env_var_opt_in(monkeypatch):
    monkeypatch.setenv("ARCGIS_CLI_ALLOW_DELETE", "1")
    guard_tool("management.Delete")          # env override -> must not raise
    guard_expr("arcpy.management.Delete('x')")  # env override -> must not raise
