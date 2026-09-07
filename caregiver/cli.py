"""`caregiver` command line."""
from __future__ import annotations

import sys
import time
from pathlib import Path

import typer

from . import db
from .config import CONFIG_TEMPLATE, load

app = typer.Typer(help="Local-first caregiver dashboard synced from MyChart (Epic FHIR).", no_args_is_help=True)
data_opt = typer.Option(None, "--data", "-d", help="Data directory (default ./data or $CAREGIVER_DATA_DIR)")


def _cfg(data: Path | None):
    cfg = load(data)
    cfg.ensure_dirs()
    return cfg


@app.command()
def init(data: Path = data_opt):
    """Create the data directory and a config.toml to edit."""
    cfg = _cfg(data)
    p = cfg.data_dir / "config.toml"
    if not p.exists():
        p.write_text(CONFIG_TEMPLATE)
        typer.echo(f"Wrote {p}")
    else:
        typer.echo(f"{p} already exists")
    db.connect(cfg.db_path).close()
    typer.echo(f"Database at {cfg.db_path}\nNext: edit config.toml (client_id, base_url), then `caregiver connect`")


@app.command()
def connect(data: Path = data_opt, no_browser: bool = typer.Option(False, help="Print URL instead of opening a browser")):
    """Authorize against MyChart (SMART on FHIR). Opens a browser; tokens saved locally."""
    from .fhir.smart import ensure_tokens
    cfg = _cfg(data)
    t = ensure_tokens(cfg.fhir, cfg.token_path, open_browser=not no_browser)
    typer.echo(f"Connected. patient={t.patient} scopes={t.scope}\nrefresh_token={'yes' if t.refresh_token else 'NO (you will re-login each sync)'}")


@app.command()
def sync(data: Path = data_opt, only: str = typer.Option("", help="Comma-separated resource types"),
         loop: str = typer.Option("", help="Repeat forever, e.g. 6h or 30m"),
         no_browser: bool = typer.Option(False)):
    """Pull everything from MyChart into the local store. Safe to run repeatedly."""
    from .fhir.sync import run_sync
    cfg = _cfg(data)
    interval = _parse_interval(loop) if loop else None
    while True:
        conn = db.connect(cfg.db_path)
        try:
            typer.echo(f"Syncing from {cfg.fhir.base_url}")
            run_sync(cfg, conn, open_browser=not no_browser, only=[s for s in only.split(",") if s] or None)
            typer.echo("Done. " + ", ".join(f"{k}={v}" for k, v in db.counts(conn).items()))
        finally:
            conn.close()
        if not interval:
            break
        typer.echo(f"Sleeping {loop}…")
        time.sleep(interval)


def _parse_interval(s: str) -> int:
    units = {"s": 1, "m": 60, "h": 3600, "d": 86400}
    return int(float(s[:-1]) * units[s[-1]]) if s and s[-1] in units else int(s)


imp = typer.Typer(help="Import offline exports (no Epic app registration needed).")
app.add_typer(imp, name="import")


@imp.command("ccda")
def import_ccda(path: Path, data: Path = data_opt):
    """Import a C-CDA XML/ZIP from MyChart 'Download' / 'Visit summary'."""
    from .importers import ccda
    cfg = _cfg(data)
    conn = db.connect(cfg.db_path)
    try:
        typer.echo(ccda.import_path(cfg, conn, path))
    finally:
        conn.close()


@imp.command("fhir")
def import_fhir(path: Path, data: Path = data_opt):
    """Import FHIR JSON (Apple Health export folder, bundles, single resources, or a zip)."""
    from .importers import fhir_bundle
    cfg = _cfg(data)
    conn = db.connect(cfg.db_path)
    try:
        typer.echo(fhir_bundle.import_path(cfg, conn, path))
    finally:
        conn.close()


imaging_app = typer.Typer(help="Imaging files (DICOM from radiology CDs, PDFs of reports).")
app.add_typer(imaging_app, name="imaging")


@imaging_app.command("index")
def imaging_index(folder: Path = typer.Argument(None), data: Path = data_opt):
    """Index data/imaging (or FOLDER) and link studies to radiology reports by date."""
    from .importers import imaging
    cfg = _cfg(data)
    conn = db.connect(cfg.db_path)
    try:
        typer.echo(imaging.index_folder(cfg, conn, folder))
    finally:
        conn.close()


@app.command()
def serve(data: Path = data_opt, port: int = typer.Option(None), host: str = "127.0.0.1"):
    """Run the dashboard at http://127.0.0.1:8080"""
    import uvicorn
    from .web.app import create_app
    cfg = _cfg(data)
    typer.echo(f"Dashboard: http://{host}:{port or cfg.web_port}")
    uvicorn.run(create_app(cfg), host=host, port=port or cfg.web_port, log_level="warning")


@app.command()
def demo(data: Path = data_opt):
    """Load a synthetic multiple-myeloma patient to evaluate the dashboard."""
    from . import demo as D
    cfg = _cfg(data)
    conn = db.connect(cfg.db_path)
    try:
        n = D.load(cfg, conn)
        typer.echo(f"Loaded demo patient ({n} rows). Run: caregiver serve" + (f" --data {data}" if data else ""))
    finally:
        conn.close()


@app.command()
def brief(data: Path = data_opt, out: Path = typer.Option(None, help="Write markdown here instead of stdout")):
    """Print / write the one-page appointment brief (markdown)."""
    from . import brief as B
    cfg = _cfg(data)
    conn = db.connect(cfg.db_path)
    try:
        md = B.markdown(conn, cfg.patient_label)
    finally:
        conn.close()
    if out:
        out.write_text(md)
        typer.echo(f"Wrote {out}")
    else:
        sys.stdout.write(md)


@app.command()
def reload(data: Path = data_opt):
    """Re-normalize from data/raw (after upgrading the mapping code)."""
    from .fhir.sync import reload_raw
    cfg = _cfg(data)
    conn = db.connect(cfg.db_path)
    try:
        typer.echo(f"Re-normalized {reload_raw(cfg, conn)} rows")
    finally:
        conn.close()


@app.command()
def status(data: Path = data_opt):
    """Row counts and last sync."""
    cfg = _cfg(data)
    conn = db.connect(cfg.db_path)
    try:
        typer.echo(f"data: {cfg.data_dir}\nlast_sync: {db.get_meta(conn, 'last_sync')}")
        for k, v in db.counts(conn).items():
            typer.echo(f"  {k:<18}{v}")
        for r in db.rows(conn, "SELECT * FROM sync_log ORDER BY id DESC LIMIT 15"):
            typer.echo(f"  {r['finished']} {r['resource_type']:<20} {r['count']:>5} {r['status']} {r['error'] or ''}")
    finally:
        conn.close()


if __name__ == "__main__":
    app()
