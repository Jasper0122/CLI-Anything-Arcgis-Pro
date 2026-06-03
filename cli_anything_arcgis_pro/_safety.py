"""Deny-by-default guard against destructive geoprocessing / expressions.

The ``gp`` passthrough can invoke the entire ArcToolbox — including
``management.Delete`` / ``DeleteFeatures`` / ``DeleteRows`` / ``TruncateTable`` —
which can destroy shapefiles, feature classes and whole file geodatabases.
This module makes deletion an *explicit, intentional* act instead of the default.

A geoprocessing **tool** is treated as destructive when its name (in any form:
``management.Delete``, ``Delete_management``, ``DeleteFeatures`` …) contains a
delete/truncate token. Tool names are identifiers, so a substring match here is
exact with no false positives — this is the solid guarantee.

A CalculateField **expression** is treated as destructive when it contains a
destructive *call* such as ``Delete(`` / ``rmtree(`` / ``unlink(``. That check is
a *footgun-guard, not a hard boundary*: ``calc`` runs arbitrary Python, so a
determined expression can evade any textual check. Matching call-style (a name
followed by ``(``) avoids false positives on ordinary field names like
``!DELETED_FLAG!`` that merely contain the substring "delete".

Opt back in (any one of):
  * pass ``allow_delete=True`` to the guard (wired to a ``--allow-delete`` flag /
    an ``allow_delete`` MCP argument), or
  * set the environment variable ``ARCGIS_CLI_ALLOW_DELETE`` to a truthy value
    (``1``, ``true``, ``yes``, ``on``).
"""

import os
import re

# Substring tokens (case-insensitive) marking a geoprocessing TOOL as destructive.
# Matching the tool name catches every spelling: management.Delete, Delete_management,
# DeleteFeatures, DeleteRows, DeleteIdentical, TruncateTable, etc.
_DESTRUCTIVE_TOOL_TOKENS = ("delete", "truncate")

# CalculateField / Python EXPRESSION guard (footgun-guard only — see module docstring).
# Match destructive *calls* (a destructive name immediately followed by "(") rather than
# a bare substring, so ordinary field names like !DELETED_FLAG! are not falsely blocked.
# Catches Delete(, .Delete(, DeleteFeatures(, truncate(, rmtree(, unlink(.
_DESTRUCTIVE_CALL_RE = re.compile(r"(?i)\b(?:delete\w*|truncate\w*|rmtree|unlink)\s*\(")

_TRUTHY = {"1", "true", "yes", "on", "y", "t"}


class DeletionBlocked(Exception):
    """Raised when a destructive operation is attempted without explicit opt-in."""


def _env_allows():
    return os.environ.get("ARCGIS_CLI_ALLOW_DELETE", "").strip().lower() in _TRUTHY


def deletion_allowed(allow_delete=False):
    """True when deletion is explicitly permitted (flag/argument or env var)."""
    return bool(allow_delete) or _env_allows()


def is_destructive_tool(tool_name):
    name = (tool_name or "").lower()
    return any(tok in name for tok in _DESTRUCTIVE_TOOL_TOKENS)


def is_destructive_expr(expr):
    return bool(_DESTRUCTIVE_CALL_RE.search(expr or ""))


def guard_tool(tool_name, allow_delete=False):
    """Raise DeletionBlocked if ``tool_name`` is destructive and not explicitly allowed."""
    if is_destructive_tool(tool_name) and not deletion_allowed(allow_delete):
        raise DeletionBlocked(
            f"Refused to run destructive tool {tool_name!r}: it deletes or truncates data "
            "(shapefiles, feature classes, geodatabase contents) and is blocked by default. "
            "To proceed intentionally, pass --allow-delete (CLI) or set ARCGIS_CLI_ALLOW_DELETE=1."
        )


def guard_expr(expr, allow_delete=False):
    """Raise DeletionBlocked if a calc expression contains a destructive call and not allowed.

    Footgun-guard only: ``calc`` evaluates arbitrary Python, so this is not a hard
    security boundary — it stops the common accidental case, not a determined one.
    The solid guarantee is the tool-name guard on ``gp`` (:func:`guard_tool`).
    """
    if is_destructive_expr(expr) and not deletion_allowed(allow_delete):
        raise DeletionBlocked(
            "Refused to evaluate a field-calculation expression containing a destructive call "
            "(e.g. Delete(), rmtree(), unlink()). Blocked by default to prevent accidental data "
            "destruction via CalculateField. To proceed intentionally, pass --allow-delete (CLI) "
            "or set ARCGIS_CLI_ALLOW_DELETE=1."
        )
