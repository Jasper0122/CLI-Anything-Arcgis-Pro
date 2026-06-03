"""Entry point for cli-anything-arcgis-pro.

Runs against ArcGIS Pro's ``arcpy``. If launched from a Python interpreter that
does NOT provide ArcPy (e.g. the CLI-Hub installs into its own Python), the
console entry point self-heals by re-dispatching into ArcGIS Pro's
``arcgispro-py3`` interpreter — so the advertised install works regardless of
which Python it landed in.
"""

import importlib.util
import os
import subprocess
import sys

# ArcPy emits localised messages; force UTF-8 stdout so JSON is consistent for agents
# regardless of the Windows console code page (e.g. GBK).
try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

import click

from . import __version__
from .cmd_batch import batch_group
from .cmd_data import data_group
from .cmd_gp import gp_cmd
from .cmd_info import info_cmd
from .cmd_layout import layout_group
from .cmd_map import map_group
from .cmd_project import project_group


@click.group(context_settings={"help_option_names": ["-h", "--help"]})
@click.version_option(__version__, prog_name="arcgis-cli")
@click.option("--json", "as_json", is_flag=True, help="Emit structured JSON (recommended for agents).")
@click.pass_context
def cli(ctx, as_json):
    """Agent-friendly CLI over ArcGIS Pro / ArcPy.

    Put --json BEFORE the subcommand: `arcgis-cli --json layout list project.aprx`.
    The `layout` group (professional export + map series) is the ArcGIS Pro edge over QGIS.
    """
    ctx.ensure_object(dict)
    ctx.obj["json"] = as_json


cli.add_command(info_cmd)
cli.add_command(project_group)
cli.add_command(layout_group)
cli.add_command(map_group)
cli.add_command(data_group)
cli.add_command(gp_cmd)
cli.add_command(batch_group)


# --- self-dispatch into ArcGIS Pro's Python when ArcPy is absent here ---

_REDISPATCH_GUARD = "_CLI_ANYTHING_ARCGIS_REDISPATCHED"


def _arcpy_available() -> bool:
    return importlib.util.find_spec("arcpy") is not None


def _find_arcgispro_python():
    """Locate ArcGIS Pro's arcgispro-py3 python.exe: env override, common paths, registry."""
    for var in ("CLI_ANYTHING_ARCGIS_PYTHON", "ARCGISPRO_PYTHON"):
        p = os.environ.get(var)
        if p and os.path.isfile(p):
            return p

    rel = r"bin\Python\envs\arcgispro-py3\python.exe"
    candidates = [
        os.path.join(r"C:\Program Files\ArcGIS\Pro", rel),
        os.path.join(os.environ.get("LOCALAPPDATA", ""), r"Programs\ArcGIS\Pro", rel),
        os.path.join(r"C:\Program Files (x86)\ArcGIS\Pro", rel),
    ]
    try:  # registry: HKLM/HKCU SOFTWARE\ESRI\ArcGISPro -> InstallDir
        import winreg

        for root in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
            try:
                with winreg.OpenKey(root, r"SOFTWARE\ESRI\ArcGISPro") as key:
                    install_dir = winreg.QueryValueEx(key, "InstallDir")[0]
                    candidates.append(os.path.join(install_dir, rel))
            except OSError:
                pass
    except ImportError:
        pass

    return next((c for c in candidates if c and os.path.isfile(c)), None)


def main():
    """Console entry point: re-dispatch into ArcGIS Pro's Python if ArcPy isn't here."""
    if not _arcpy_available() and not os.environ.get(_REDISPATCH_GUARD):
        target = _find_arcgispro_python()
        if target and os.path.abspath(target) != os.path.abspath(sys.executable):
            pkg_parent = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            env = dict(os.environ)
            env[_REDISPATCH_GUARD] = "1"
            existing = env.get("PYTHONPATH")
            env["PYTHONPATH"] = pkg_parent + (os.pathsep + existing if existing else "")
            proc = subprocess.run(
                [target, "-m", "cli_anything_arcgis_pro", *sys.argv[1:]], env=env
            )
            sys.exit(proc.returncode)
        # ArcGIS Pro python not found — fall through; commands will report a clear
        # ArcPy error, while --help / --version still work.
    cli()


if __name__ == "__main__":
    main()
