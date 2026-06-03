"""``arcgis-cli map`` — author map content: add data and apply symbology.

The cartographic-authoring half of the agent loop. `gp` produces an output,
`map add-data` puts it on a map, and `map symbology` makes it communicate — so
an agent can go analyze -> add -> color -> `layout export` with no GUI clicks.

All commands operate on an `.aprx` on disk (headless ArcPy) and **save in place**
by default. The live-session (.NET bridge) equivalents are tracked in ROADMAP.md.

ArcPy symbology gotchas, learned the hard way and guarded for here:
  * Never cache ``sym.renderer`` — read ``sym.renderer.<prop>`` fresh each time;
    a cached reference goes stale after the first assignment.
  * ``updateRenderer`` needs a **valid data source with the field present** — it
    reads the data to classify. If the source is broken it silently leaves a
    SimpleRenderer, so we verify the switch actually took and error out if not.
"""

import click

from ._io import arcgis_command


def _get_map(proj, map_name):
    maps = proj.listMaps(map_name) if map_name else proj.listMaps()
    if not maps:
        raise ValueError(
            f"No map matching {map_name!r}" if map_name else "Project has no maps"
        )
    return maps[0]


def _get_layer(m, layer_name):
    """Resolve a layer by name (exact match preferred over wildcard)."""
    matches = m.listLayers(layer_name)
    exact = [l for l in matches if l.name == layer_name]
    chosen = exact[0] if exact else (matches[0] if matches else None)
    if chosen is None:
        raise ValueError(f"No layer matching {layer_name!r} in map {m.name!r}")
    return chosen


def _symbology_of(lyr):
    """Return a layer's symbology object, or a clear error if it can't be symbolized."""
    if not getattr(lyr, "isFeatureLayer", False):
        raise ValueError(f"Layer {lyr.name!r} is not a feature layer; can't symbolize it.")
    sym = lyr.symbology
    if not hasattr(sym, "updateRenderer"):
        raise ValueError(f"Layer {lyr.name!r} does not support renderers.")
    return sym


def _require_field(lyr, field):
    import arcpy

    names = {f.name.lower() for f in arcpy.ListFields(lyr.dataSource)}
    if field.lower() not in names:
        raise ValueError(
            f"Field {field!r} not found on {lyr.name!r}. Available: "
            + ", ".join(sorted(f.name for f in arcpy.ListFields(lyr.dataSource)))
        )


def _resolve_ramp(proj, ramp):
    ramps = proj.listColorRamps(ramp)
    if not ramps:
        raise ValueError(
            f"No color ramp named {ramp!r}. Try a built-in like 'Viridis', "
            "'Inferno', 'Reds (Continuous)'."
        )
    return ramps[0]


@click.group("map")
def map_group():
    """Author map content (add data, symbology) in an .aprx."""


@map_group.command("add-data")
@click.argument("aprx", type=click.Path(exists=True, dir_okay=False))
@click.argument("data_path")
@click.option("--map", "map_name", default=None, help="Target map (default: first map).")
@click.option("--name", "layer_name", default=None, help="Rename the added layer.")
@arcgis_command()
def add_data_cmd(aprx, data_path, map_name, layer_name):
    """Add a dataset (feature class / shapefile / layer file) to a map, then save.

    Closes the loop after `gp`: the tool's output becomes a visible layer.
    """
    import arcpy

    if not arcpy.Exists(data_path):
        raise ValueError(f"Data source does not exist: {data_path!r}")

    proj = arcpy.mp.ArcGISProject(aprx)
    m = _get_map(proj, map_name)
    before = len(m.listLayers())
    lyr = m.addDataFromPath(data_path)
    if layer_name:
        lyr.name = layer_name
    proj.save()
    return {
        "aprx": aprx,
        "map": m.name,
        "added": lyr.name,
        "source": data_path,
        "layerCount": len(m.listLayers()),
        "addedLayers": len(m.listLayers()) - before,
    }


@map_group.group("symbology")
def symbology_group():
    """Apply a renderer so the map communicates (graduated colors, unique values)."""


@symbology_group.command("graduated")
@click.argument("aprx", type=click.Path(exists=True, dir_okay=False))
@click.argument("layer")
@click.option("--field", required=True, help="Numeric field to classify.")
@click.option("--classes", default=5, show_default=True, help="Number of class breaks.")
@click.option(
    "--method",
    default="NaturalBreaks",
    show_default=True,
    help="Classification method: NaturalBreaks, EqualInterval, Quantile, "
    "GeometricInterval, StandardDeviation.",
)
@click.option("--ramp", default=None, help="Color ramp name, e.g. 'Viridis' (optional).")
@click.option("--map", "map_name", default=None, help="Target map (default: first map).")
@arcgis_command()
def sym_graduated_cmd(aprx, layer, field, classes, method, ramp, map_name):
    """Graduated (class-breaks) colors on a quantitative field — the workhorse thematic map."""
    proj = __import__("arcpy").mp.ArcGISProject(aprx)
    m = _get_map(proj, map_name)
    lyr = _get_layer(m, layer)
    _require_field(lyr, field)

    sym = _symbology_of(lyr)
    sym.updateRenderer("GraduatedColorsRenderer")
    # Never cache sym.renderer — assign through it each time.
    sym.renderer.classificationField = field
    sym.renderer.breakCount = classes
    if method:
        sym.renderer.classificationMethod = method
    if ramp:
        sym.renderer.colorRamp = _resolve_ramp(proj, ramp)
    lyr.symbology = sym

    # Guard the silent-failure case: a broken data source leaves a SimpleRenderer.
    applied = lyr.symbology.renderer.type
    if applied != "GraduatedColorsRenderer":
        raise RuntimeError(
            f"Renderer did not switch (still {applied!r}). Is {lyr.name!r}'s data "
            f"source valid and field {field!r} numeric? Source: {lyr.dataSource}"
        )
    proj.save()
    return {
        "aprx": aprx,
        "map": m.name,
        "layer": lyr.name,
        "renderer": "GraduatedColorsRenderer",
        "field": field,
        "classes": lyr.symbology.renderer.breakCount,
        "method": method,
        "ramp": ramp,
    }


@symbology_group.command("unique")
@click.argument("aprx", type=click.Path(exists=True, dir_okay=False))
@click.argument("layer")
@click.option("--field", required=True, help="Categorical field to symbolize by value.")
@click.option("--ramp", default=None, help="Color ramp name, e.g. 'Viridis' (optional).")
@click.option("--map", "map_name", default=None, help="Target map (default: first map).")
@arcgis_command()
def sym_unique_cmd(aprx, layer, field, ramp, map_name):
    """Unique-values renderer on a categorical field (one color per distinct value)."""
    proj = __import__("arcpy").mp.ArcGISProject(aprx)
    m = _get_map(proj, map_name)
    lyr = _get_layer(m, layer)
    _require_field(lyr, field)

    sym = _symbology_of(lyr)
    sym.updateRenderer("UniqueValueRenderer")
    sym.renderer.fields = [field]
    if ramp:
        sym.renderer.colorRamp = _resolve_ramp(proj, ramp)
    lyr.symbology = sym

    applied = lyr.symbology.renderer.type
    if applied != "UniqueValueRenderer":
        raise RuntimeError(
            f"Renderer did not switch (still {applied!r}). Is {lyr.name!r}'s data "
            f"source valid and field {field!r} present? Source: {lyr.dataSource}"
        )
    proj.save()
    return {
        "aprx": aprx,
        "map": m.name,
        "layer": lyr.name,
        "renderer": "UniqueValueRenderer",
        "field": field,
        "groups": len(lyr.symbology.renderer.groups),
        "ramp": ramp,
    }
