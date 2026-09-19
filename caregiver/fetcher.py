"""Open the patient portal in a real browser on your own machine and catch what you download.

This never handles a password. It opens a genuine browser window pointed at the portal; you log in
yourself, including whatever one-time code the portal texts you. The profile is kept between runs,
so once you tell the portal to remember the device, later runs usually skip straight past the code.
Anything the page downloads is captured and imported.

Requires the optional browser extra:  pip install 'caregiver-dashboard[fetch]' && playwright install chromium
"""
from __future__ import annotations

import os
import re
import sqlite3
import time
from pathlib import Path
from typing import Callable

from .config import Config
from .ingest import already_imported, detect_kind, file_digest, import_any, record_import

# Link text seen on the download path across MyChart deployments. Tried in order, best effort only:
# failing to find any of these is normal and simply hands control back to you.
NAV_CANDIDATES = [
    r"sharing hub", r"share my record", r"document center", r"download my record",
    r"request record", r"visit records", r"health summary", r"download",
]
LOGGED_IN_HINTS = [r"/home", r"menu", r"sign out", r"log out"]


class FetchError(RuntimeError):
    pass


def _require_playwright():
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as e:
        raise FetchError(
            "Playwright is not installed. Run:\n"
            "  pip install 'caregiver-dashboard[fetch]'\n"
            "  playwright install chromium") from e
    return sync_playwright


def fetch(cfg: Config, url: str, timeout: int = 900, auto_navigate: bool = True,
          on_event: Callable[[str, dict], None] | None = None) -> list[Path]:
    """Open `url`, wait for you to drive it, and capture every download. Returns saved paths."""
    sync_playwright = _require_playwright()
    profile = cfg.browser_dir
    profile.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(profile, 0o700)          # the profile holds a logged-in portal session
    except OSError:
        pass
    dest = cfg.downloads_dir
    dest.mkdir(parents=True, exist_ok=True)

    say = on_event or (lambda k, d: None)
    saved: list[Path] = []

    with sync_playwright() as pw:
        try:
            ctx = pw.chromium.launch_persistent_context(
                user_data_dir=str(profile), headless=False, accept_downloads=True,
                downloads_path=str(dest), args=["--disable-blink-features=AutomationControlled"])
        except Exception as e:
            raise FetchError(f"Could not start a browser: {e}\nTry: playwright install chromium") from e

        def on_download(download):
            target = dest / download.suggested_filename
            n = 1
            while target.exists():
                target = dest / f"{Path(download.suggested_filename).stem}-{n}{Path(download.suggested_filename).suffix}"
                n += 1
            try:
                download.save_as(str(target))
                saved.append(target)
                say("downloaded", {"path": target})
            except Exception as e:
                say("download_failed", {"error": str(e)})

        ctx.on("page", lambda p: p.on("download", on_download))
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.on("download", on_download)

        say("opening", {"url": url})
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=60_000)
        except Exception as e:
            say("nav_warning", {"error": str(e)})

        say("await_login", {})
        deadline = time.time() + timeout
        navigated = False
        while time.time() < deadline:
            if saved:
                time.sleep(2)             # let a second file finish if the portal sends several
                break
            if auto_navigate and not navigated and _looks_logged_in(page):
                navigated = _try_navigate(page, say)
            if ctx.pages and all(p.is_closed() for p in ctx.pages):
                break
            time.sleep(1.5)

        try:
            ctx.close()
        except Exception:
            pass

    if not saved:
        say("nothing", {})
    return saved


def _looks_logged_in(page) -> bool:
    try:
        body = (page.url or "") + " " + (page.title() or "")
    except Exception:
        return False
    return any(re.search(h, body, re.I) for h in LOGGED_IN_HINTS)


def _try_navigate(page, say) -> bool:
    """Best-effort nudge toward the download page. Never fatal: you can always click yourself."""
    for pattern in NAV_CANDIDATES:
        try:
            link = page.get_by_role("link", name=re.compile(pattern, re.I)).first
            if link.count() == 0:
                continue
            say("navigating", {"to": pattern})
            link.click(timeout=5000)
            page.wait_for_load_state("domcontentloaded", timeout=15_000)
            return True
        except Exception:
            continue
    return False


def fetch_and_import(cfg: Config, conn: sqlite3.Connection, url: str, timeout: int = 900,
                     on_event: Callable[[str, dict], None] | None = None) -> list[dict]:
    say = on_event or (lambda k, d: None)
    results = []
    for path in fetch(cfg, url, timeout=timeout, on_event=on_event):
        kind = detect_kind(path)
        if kind in ("unknown", "empty"):
            say("skipped", {"path": path, "kind": kind})
            continue
        digest = file_digest(path)
        if already_imported(conn, digest):
            say("duplicate", {"path": path})
            continue
        result = import_any(cfg, conn, path)
        record_import(conn, path, digest, result)
        results.append(result)
        say("imported", result)
    return results
