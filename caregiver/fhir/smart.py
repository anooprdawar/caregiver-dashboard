"""SMART on FHIR standalone patient launch (public client + PKCE) against Epic.

Flow: discover endpoints -> open browser -> catch redirect on localhost -> exchange code -> store
tokens with 0600 perms. Refresh when possible; otherwise re-launch the browser.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import threading
import time
import webbrowser
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse

import httpx

from ..config import FhirConfig


class AuthError(RuntimeError):
    pass


@dataclass
class Tokens:
    access_token: str
    expires_at: float
    refresh_token: str | None
    patient: str | None
    scope: str | None
    id_token: str | None = None

    def expired(self, skew: int = 60) -> bool:
        return time.time() >= self.expires_at - skew

    def to_json(self) -> dict:
        return self.__dict__.copy()

    @classmethod
    def from_json(cls, d: dict) -> "Tokens":
        return cls(**{k: d.get(k) for k in cls.__dataclass_fields__})


def discover(base_url: str) -> tuple[str, str]:
    """Return (authorize_url, token_url)."""
    with httpx.Client(timeout=30, follow_redirects=True) as c:
        r = c.get(base_url + ".well-known/smart-configuration", headers={"Accept": "application/json"})
        if r.status_code == 200:
            j = r.json()
            if j.get("authorization_endpoint") and j.get("token_endpoint"):
                return j["authorization_endpoint"], j["token_endpoint"]
        r = c.get(base_url + "metadata", headers={"Accept": "application/fhir+json"})
        r.raise_for_status()
        cs = r.json()
        for rest in cs.get("rest", []):
            for ext in ((rest.get("security") or {}).get("extension") or []):
                if ext.get("url", "").endswith("oauth-uris"):
                    d = {e["url"]: e.get("valueUri") for e in ext.get("extension", [])}
                    if d.get("authorize") and d.get("token"):
                        return d["authorize"], d["token"]
    raise AuthError("Could not discover OAuth endpoints from smart-configuration or CapabilityStatement")


def _pkce() -> tuple[str, str]:
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(64)).rstrip(b"=").decode()
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    return verifier, challenge


class _Catcher(BaseHTTPRequestHandler):
    result: dict = {}

    def do_GET(self):  # noqa: N802
        u = urlparse(self.path)
        if u.path != "/callback":
            self.send_response(404)
            self.end_headers()
            return
        q = {k: v[0] for k, v in parse_qs(u.query).items()}
        _Catcher.result = q
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        msg = "Connected. You can close this tab." if "code" in q else f"Authorization failed: {q}"
        self.wfile.write(f"<html><body style='font-family:sans-serif'><h2>{msg}</h2></body></html>".encode())

    def log_message(self, *a):  # silence
        pass


def authorize(cfg: FhirConfig, open_browser: bool = True, timeout: int = 300) -> Tokens:
    if not cfg.client_id:
        raise AuthError("fhir.client_id is empty. Register a patient-facing app at https://fhir.epic.com and paste "
                        "the client ID into data/config.toml")
    auth_url, token_url = discover(cfg.base_url)
    verifier, challenge = _pkce()
    state = secrets.token_urlsafe(16)
    params = {
        "response_type": "code", "client_id": cfg.client_id, "redirect_uri": cfg.redirect_uri,
        "scope": " ".join(cfg.scopes), "state": state, "aud": cfg.base_url.rstrip("/"),
        "code_challenge": challenge, "code_challenge_method": "S256",
    }
    url = f"{auth_url}?{urlencode(params)}"
    _Catcher.result = {}
    server = HTTPServer(("127.0.0.1", cfg.redirect_port), _Catcher)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    print("\nOpen this URL in a browser and log in to MyChart:\n\n  " + url + "\n")
    if open_browser:
        webbrowser.open(url)
    deadline = time.time() + timeout
    try:
        while time.time() < deadline and not _Catcher.result:
            time.sleep(0.25)
    finally:
        server.shutdown()
    res = _Catcher.result
    if not res:
        raise AuthError("Timed out waiting for the MyChart redirect")
    if res.get("state") != state:
        raise AuthError("State mismatch on redirect; aborting")
    if "code" not in res:
        raise AuthError(f"Authorization denied: {res.get('error')} {res.get('error_description', '')}")
    return _exchange(token_url, {
        "grant_type": "authorization_code", "code": res["code"], "redirect_uri": cfg.redirect_uri,
        "client_id": cfg.client_id, "code_verifier": verifier,
    })


def _exchange(token_url: str, form: dict) -> Tokens:
    with httpx.Client(timeout=30) as c:
        r = c.post(token_url, data=form, headers={"Accept": "application/json"})
    if r.status_code != 200:
        raise AuthError(f"Token endpoint returned {r.status_code}: {r.text[:500]}")
    j = r.json()
    return Tokens(access_token=j["access_token"], expires_at=time.time() + int(j.get("expires_in", 3600)),
                  refresh_token=j.get("refresh_token"), patient=j.get("patient"), scope=j.get("scope"),
                  id_token=j.get("id_token"))


def refresh(cfg: FhirConfig, tokens: Tokens) -> Tokens:
    if not tokens.refresh_token:
        raise AuthError("No refresh token")
    _, token_url = discover(cfg.base_url)
    new = _exchange(token_url, {"grant_type": "refresh_token", "refresh_token": tokens.refresh_token,
                                "client_id": cfg.client_id})
    new.patient = new.patient or tokens.patient
    new.refresh_token = new.refresh_token or tokens.refresh_token
    return new


def load_tokens(path: Path) -> Tokens | None:
    if not path.exists():
        return None
    try:
        return Tokens.from_json(json.loads(path.read_text()))
    except Exception:
        return None


def save_tokens(path: Path, tokens: Tokens) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(tokens.to_json(), f)


def ensure_tokens(cfg: FhirConfig, path: Path, open_browser: bool = True) -> Tokens:
    """Return valid tokens: cached -> refreshed -> interactive."""
    t = load_tokens(path)
    if t and not t.expired():
        return t
    if t and t.refresh_token:
        try:
            t = refresh(cfg, t)
            save_tokens(path, t)
            return t
        except AuthError as e:
            print(f"Refresh failed ({e}); re-authorizing interactively")
    t = authorize(cfg, open_browser=open_browser)
    save_tokens(path, t)
    return t
