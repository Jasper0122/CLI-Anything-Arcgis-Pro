"""End-to-end tests — require a licensed ArcGIS Pro (ArcPy) install.

Skipped automatically when ArcPy is unavailable, so the suite still passes in CI
without a backend. Run these with ArcGIS Pro's `arcgispro-py3` Python.
"""

import importlib.util
import json
import os
import shutil

import pytest
from click.testing import CliRunner

from cli_anything_arcgis_pro.__main__ import cli

arcpy_available = importlib.util.find_spec("arcpy") is not None
requires_arcpy = pytest.mark.skipif(
    not arcpy_available, reason="ArcGIS Pro / ArcPy not available"
)


def _blank_aprx_template():
    """Locate a blank .aprx shipped with ArcGIS Pro (for building a scratch project)."""
    candidates = []
    for base in (
        os.environ.get("CLI_ANYTHING_ARCGIS_HOME"),
        r"C:\Program Files\ArcGIS\Pro",
        r"C:\Program Files (x86)\ArcGIS\Pro",
    ):
        if base:
            candidates.append(
                os.path.join(
                    base,
                    r"Resources\ArcToolBox\Services\routingservices\data\Blank.aprx",
                )
            )
    return next((c for c in candidates if os.path.isfile(c)), None)


def _build_sample_project(tmp_path):
    """Build a scratch FGDB (point fc with numeric + categorical fields) and an
    .aprx containing it. Returns (aprx_path, fc_path) or skips if no template."""
    import arcpy

    template = _blank_aprx_template()
    if not template:
        pytest.skip("no blank .aprx template found to build a scratch project")

    gdb = str(tmp_path / "scratch.gdb")
    arcpy.management.CreateFileGDB(str(tmp_path), "scratch.gdb")
    fc = os.path.join(gdb, "places")
    arcpy.management.CreateFeatureclass(
        gdb, "places", "POINT", spatial_reference=arcpy.SpatialReference(4326)
    )
    arcpy.management.AddField(fc, "POP", "LONG")
    arcpy.management.AddField(fc, "CAT", "TEXT", field_length=20)
    rows = [
        (100, "A", (0, 0)), (5000, "B", (1, 1)), (20000, "A", (2, 2)),
        (80000, "C", (3, 3)), (300000, "B", (4, 4)), (1200000, "C", (5, 5)),
    ]
    with arcpy.da.InsertCursor(fc, ["POP", "CAT", "SHAPE@XY"]) as cur:
        for r in rows:
            cur.insertRow(r)

    aprx_path = str(tmp_path / "scratch.aprx")
    shutil.copyfile(template, aprx_path)
    aprx = arcpy.mp.ArcGISProject(aprx_path)
    aprx.listMaps()[0].addDataFromPath(fc)
    aprx.save()
    return aprx_path, fc


def _renderer_type(aprx_path, layer_name):
    """Read the persisted renderer, releasing the .aprx lock before returning.

    ArcGISProject holds a file lock; in an in-process test we must drop it or the
    next command that saves the same .aprx fails with an OSError.
    """
    import gc

    import arcpy

    proj = arcpy.mp.ArcGISProject(aprx_path)
    lyr = [l for l in proj.listMaps()[0].listLayers() if l.name == layer_name][0]
    rtype = lyr.symbology.renderer.type
    del lyr, proj
    gc.collect()
    return rtype


@requires_arcpy
def test_info_reports_license_and_version():
    result = CliRunner().invoke(cli, ["--json", "info"])
    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["ok"] is True
    data = payload["data"]
    assert "arcpy_version" in data
    assert "product_license" in data
    assert "extensions" in data


@requires_arcpy
def test_bad_dataset_returns_structured_error():
    result = CliRunner().invoke(
        cli, ["--json", "data", "describe", r"C:\does\not\exist.gdb\nope"]
    )
    payload = json.loads(result.output)
    assert payload["ok"] is False
    assert "error" in payload


def _invoke(args):
    result = CliRunner().invoke(cli, ["--json", *args])
    return json.loads(result.output)


@requires_arcpy
def test_map_add_data_then_symbology_persists(tmp_path):
    """The cartographic-authoring loop: add a layer, then graduated + unique
    symbology, and confirm each renderer persists to the saved .aprx."""
    aprx, fc = _build_sample_project(tmp_path)

    # add-data: the same fc added again under a new name
    r = _invoke(["map", "add-data", aprx, fc, "--name", "Copy of places"])
    assert r["ok"], r
    assert r["data"]["added"] == "Copy of places"

    # graduated colors on a numeric field, persisted
    r = _invoke(["map", "symbology", "graduated", aprx, "places",
                 "--field", "POP", "--classes", "4"])
    assert r["ok"], r
    assert r["data"]["renderer"] == "GraduatedColorsRenderer"
    assert _renderer_type(aprx, "places") == "GraduatedColorsRenderer"

    # unique values on a categorical field, persisted
    r = _invoke(["map", "symbology", "unique", aprx, "places", "--field", "CAT"])
    assert r["ok"], r
    assert _renderer_type(aprx, "places") == "UniqueValueRenderer"


@requires_arcpy
def test_symbology_bad_field_errors_cleanly(tmp_path):
    """A non-existent field must fail loudly, not silently leave a SimpleRenderer."""
    aprx, _ = _build_sample_project(tmp_path)
    r = _invoke(["map", "symbology", "graduated", aprx, "places", "--field", "NOPE"])
    assert r["ok"] is False
    assert "NOPE" in r["error"]
