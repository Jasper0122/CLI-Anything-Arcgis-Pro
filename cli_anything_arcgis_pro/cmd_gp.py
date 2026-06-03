"""``arcgis-cli gp`` — run any geoprocessing tool generically.

Lets an agent invoke the full ArcToolbox surface (analysis, management, sa, ...)
without a bespoke subcommand per tool.
"""

import json

import click

from ._io import arcgis_command
from ._safety import guard_tool


def _resolve_tool(arcpy, name):
    """Resolve a tool by dotted path ('analysis.Buffer') or legacy name ('Buffer_analysis')."""
    obj = arcpy
    for part in name.split("."):
        obj = getattr(obj, part)
    if not callable(obj):
        raise ValueError(f"{name!r} is not a callable tool")
    return obj


@click.command("gp")
@click.argument("tool")
@click.option("--arg", "-a", "positional", multiple=True, help="Positional argument (repeatable, in order).")
@click.option("--kw", "kw_pairs", multiple=True, help='Named arg as KEY=VALUE (repeatable, shell-friendly). e.g. --kw buffer_distance_or_field="100 Meters".')
@click.option("--kwargs", "kwargs_json", default=None, help='Named args as a JSON object (alternative to --kw).')
@click.option("--checkout", "extensions", multiple=True, help="Extension to check out first, e.g. Spatial (repeatable).")
@click.option("--allow-delete", "allow_delete", is_flag=True, default=False, help="Explicitly permit destructive tools (Delete/Truncate/...). Blocked by default.")
@arcgis_command()
def gp_cmd(tool, positional, kw_pairs, kwargs_json, extensions, allow_delete):
    """Run a geoprocessing tool. TOOL is e.g. 'analysis.Buffer' or 'Buffer_analysis'.

    Example:
      arcgis-cli gp analysis.Buffer -a roads.shp -a roads_buf.shp --kw buffer_distance_or_field="100 Meters"
    """
    import arcpy

    # Deny-by-default: refuse Delete/Truncate-style tools unless explicitly opted in.
    guard_tool(tool, allow_delete)

    arcpy.env.overwriteOutput = True  # agents commonly re-run tools onto the same output

    checked = []
    for ext in extensions:
        status = arcpy.CheckOutExtension(ext)
        checked.append({"extension": ext, "status": status})

    kwargs = json.loads(kwargs_json) if kwargs_json else {}
    for pair in kw_pairs:
        if "=" not in pair:
            raise ValueError(f"--kw expects KEY=VALUE, got {pair!r}")
        key, val = pair.split("=", 1)
        kwargs[key.strip()] = val
    fn = _resolve_tool(arcpy, tool)
    result = fn(*positional, **kwargs)

    # Result object -> list of output strings
    outputs = []
    try:
        for i in range(result.outputCount):
            outputs.append(result.getOutput(i))
    except Exception:
        outputs = [str(result)]

    for ext in extensions:
        arcpy.CheckInExtension(ext)

    return {
        "tool": tool,
        "checkedOut": checked,
        "messages": arcpy.GetMessages(),
        "outputs": outputs,
    }
