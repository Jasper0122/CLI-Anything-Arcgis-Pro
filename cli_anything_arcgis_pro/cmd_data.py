"""``arcgis-cli data`` — describe, query, count and edit feature classes / tables."""

import json

import click

from ._io import arcgis_command
from ._safety import guard_expr


@click.group("data")
def data_group():
    """Inspect and edit vector data (feature classes, shapefiles, tables)."""


@data_group.command("describe")
@click.argument("dataset")
@arcgis_command()
def describe_cmd(dataset):
    """Describe a dataset: type, geometry, spatial reference, extent."""
    import arcpy

    d = arcpy.Describe(dataset)
    out = {"dataType": d.dataType, "name": getattr(d, "name", None)}
    for attr in ("shapeType", "datasetType", "catalogPath"):
        out[attr] = getattr(d, attr, None)
    sr = getattr(d, "spatialReference", None)
    if sr is not None:
        out["spatialReference"] = {"name": sr.name, "factoryCode": sr.factoryCode}
    ext = getattr(d, "extent", None)
    if ext is not None:
        out["extent"] = {"XMin": ext.XMin, "YMin": ext.YMin, "XMax": ext.XMax, "YMax": ext.YMax}
    return out


@data_group.command("fields")
@click.argument("dataset")
@arcgis_command()
def fields_cmd(dataset):
    """List fields with type, alias and length."""
    import arcpy

    return {
        "dataset": dataset,
        "fields": [
            {"name": f.name, "type": f.type, "alias": f.aliasName, "length": f.length}
            for f in arcpy.ListFields(dataset)
        ],
    }


@data_group.command("count")
@click.argument("dataset")
@click.option("--where", default=None, help="SQL where clause to filter the count.")
@arcgis_command()
def count_cmd(dataset, where):
    """Count rows, optionally filtered by a where clause."""
    import arcpy

    if where:
        n = 0
        with arcpy.da.SearchCursor(dataset, ["OID@"], where_clause=where) as cur:
            for _ in cur:
                n += 1
    else:
        n = int(arcpy.management.GetCount(dataset)[0])
    return {"dataset": dataset, "where": where, "count": n}


@data_group.command("query")
@click.argument("dataset")
@click.option("--where", default=None, help="SQL where clause.")
@click.option("--fields", default="*", help='Comma-separated field list, or "*" for all.')
@click.option("--limit", default=100, show_default=True, help="Max rows (0 = no limit).")
@click.option("--geometry/--no-geometry", default=False, help="Include WKT geometry.")
@arcgis_command()
def query_cmd(dataset, where, fields, limit, geometry):
    """Run an attribute query and return rows as JSON."""
    import arcpy

    if fields.strip() == "*":
        field_list = [f.name for f in arcpy.ListFields(dataset) if f.type != "Geometry"]
    else:
        field_list = [f.strip() for f in fields.split(",") if f.strip()]

    cursor_fields = list(field_list)
    if geometry:
        cursor_fields.append("SHAPE@WKT")

    rows = []
    with arcpy.da.SearchCursor(dataset, cursor_fields, where_clause=where) as cur:
        for i, rec in enumerate(cur):
            if limit and i >= limit:
                break
            row = dict(zip(field_list, rec[: len(field_list)]))
            if geometry:
                row["_geometry_wkt"] = rec[-1]
            rows.append(row)
    return {
        "dataset": dataset,
        "where": where,
        "returned": len(rows),
        "limited": bool(limit) and len(rows) >= limit,
        "rows": rows,
    }


@data_group.command("calc")
@click.argument("dataset")
@click.option("--field", required=True, help="Field to calculate.")
@click.option("--expr", required=True, help='Python expression, e.g. "!POP! * 2".')
@click.option("--where", default=None, help="Limit calculation to matching rows.")
@click.option("--allow-delete", "allow_delete", is_flag=True, default=False, help="Permit delete/remove/truncate tokens in the expression. Blocked by default.")
@arcgis_command()
def calc_cmd(dataset, field, expr, where, allow_delete):
    """Calculate a field with arcpy.management.CalculateField (Python 3)."""
    import arcpy

    # Deny-by-default: refuse expressions that look like they delete/remove data.
    guard_expr(expr, allow_delete)

    # CalculateField has no where clause, so scope it via a selected layer/view.
    target = dataset
    scratch = None
    if where:
        scratch = "arcgiscli_calc_view"
        if arcpy.Exists(scratch):
            arcpy.management.Delete(scratch)
        try:
            arcpy.management.MakeFeatureLayer(dataset, scratch, where_clause=where)
        except Exception:
            arcpy.management.MakeTableView(dataset, scratch, where_clause=where)
        target = scratch

    try:
        arcpy.management.CalculateField(
            in_table=target,
            field=field,
            expression=expr,
            expression_type="PYTHON3",
        )
        affected = int(arcpy.management.GetCount(target)[0])
    finally:
        if scratch and arcpy.Exists(scratch):
            arcpy.management.Delete(scratch)

    return {"dataset": dataset, "field": field, "expr": expr, "where": where, "rowsAffected": affected}
