#!/usr/bin/env python3
"""memedl — receive an Instagram (or any gallery-dl supported) URL and fetch its media.

Stdlib only. Intake:  POST /hook  (plain text body, JSON {"url": ...}, or form url=...)
Gallery:  GET  /            -> HTML grid of downloaded files
File:     GET  /f/<name>    -> raw file
Health:   GET  /healthz

Config via env:
  MEMEDL_DIR      destination folder            (default ~/memes)
  MEMEDL_PORT     listen port                   (default 8765)
  MEMEDL_TOKEN    shared secret; require ?token= or X-Token header (default: generated at startup and printed)
  MEMEDL_COOKIES  cookies.txt for instagram     (optional)
  MEMEDL_BIND     bind address                  (default 127.0.0.1)
  MEMEDL_XBB_URL  XBackBone base URL, e.g. https://korobase.info/xbackbone  (optional: mirror uploads)
  MEMEDL_XBB_TOKEN_FILE  file holding the XBackBone API token               (required for mirroring)
"""
import json
import mimetypes
import os
import re
import secrets
import shutil
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote, urlparse

DIR = Path(os.environ.get("MEMEDL_DIR") or Path.home() / "memes").expanduser()
PORT = int(os.environ.get("MEMEDL_PORT", "8765"))
BIND = os.environ.get("MEMEDL_BIND", "127.0.0.1")
TOKEN = os.environ.get("MEMEDL_TOKEN") or secrets.token_urlsafe(12)
COOKIES = os.environ.get("MEMEDL_COOKIES", "")
# Les sites servent souvent du WebP ; ce réglage convertit à l'arrivée.
# "png" (défaut), "jpg" (bien plus léger) ou "" pour ne rien convertir.
WEBP_TO = os.environ.get("MEMEDL_WEBP_TO", "png").strip().lower()
XBB_URL = (os.environ.get("MEMEDL_XBB_URL") or "").rstrip("/")
XBB_TOKEN_FILE = os.environ.get("MEMEDL_XBB_TOKEN_FILE", "")
GDL = shutil.which("gallery-dl") or "/opt/memedl-venv/bin/gallery-dl"
LOG = DIR.parent / "memedl.log"
SEEN = DIR.parent / "memedl-seen.txt"
LINKS = DIR.parent / "memedl-links.jsonl"

DIR.mkdir(parents=True, exist_ok=True)
SEEN.touch(exist_ok=True)
QUEUE: "list[tuple[str, str]]" = []
QLOCK = threading.Lock()
STATS = {"ok": 0, "fail": 0, "running": None}


def log(msg: str) -> None:
    line = time.strftime("%Y-%m-%d %H:%M:%S ") + msg
    print(line, flush=True)
    try:
        with LOG.open("a") as fh:
            fh.write(line + "\n")
    except OSError:
        pass


def already_seen(url: str) -> bool:
    key = url.split("?")[0].rstrip("/")
    return any(l.strip() == key for l in SEEN.read_text().splitlines())


def remember(url: str) -> None:
    with SEEN.open("a") as fh:
        fh.write(url.split("?")[0].rstrip("/") + "\n")


def slug(url: str) -> str:
    m = re.search(r"/(?:p|reel|reels|tv)/([A-Za-z0-9_-]+)", url)
    if m:
        return m.group(1)
    tail = urlparse(url).path.rstrip("/").split("/")[-1] or "item"
    return re.sub(r"[^A-Za-z0-9_-]", "_", tail)[:40]


def xbb_token() -> str:
    if not (XBB_URL and XBB_TOKEN_FILE):
        return ""
    try:
        return Path(XBB_TOKEN_FILE).read_text().strip()
    except OSError:
        return ""


def upload_to_xbb(path: Path) -> str:
    """Mirror one file into XBackBone (ShareX-style /upload) and return its share URL.

    Contract verified against XBackBone 3.8.2: form fields `token` + `upload` (a `files[]`
    field makes the app throw a Slim 500), reply `{"message":"OK","url":...}`.
    """
    token = xbb_token()
    if not token:
        return ""
    boundary = "----memedl" + uuid.uuid4().hex
    ctype = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    body = b"".join([
        f'--{boundary}\r\nContent-Disposition: form-data; name="token"\r\n\r\n{token}\r\n'.encode(),
        (f'--{boundary}\r\nContent-Disposition: form-data; name="upload"; '
         f'filename="{path.name}"\r\nContent-Type: {ctype}\r\n\r\n').encode(),
        path.read_bytes(),
        f"\r\n--{boundary}--\r\n".encode(),
    ])
    req = urllib.request.Request(
        f"{XBB_URL}/upload", data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}",
                 "User-Agent": "memedl/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=180) as resp:
            raw = resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        log(f"XBB FAIL {path.name} http={exc.code} {exc.read()[:200]!r}")
        return ""
    except Exception as exc:  # noqa: BLE001 - network layer
        log(f"XBB FAIL {path.name} {exc}")
        return ""
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        log(f"XBB FAIL {path.name} non-json {raw[:200]!r}")
        return ""
    if data.get("message") != "OK":
        log(f"XBB FAIL {path.name} message={data.get('message')!r}")
        return ""
    url = data.get("url") or ""
    if not url:
        log(f"XBB FAIL {path.name} no url {raw[:200]!r}")
        return ""
    with LINKS.open("a") as fh:
        fh.write(json.dumps({"file": path.name, "url": url}) + "\n")
    log(f"XBB OK {path.name} {url}")
    return url


def load_links() -> dict:
    out: dict = {}
    try:
        for line in LINKS.read_text().splitlines():
            try:
                rec = json.loads(line)
                out[rec["file"]] = rec["url"]
            except (json.JSONDecodeError, KeyError):
                continue
    except OSError:
        pass
    return out


def convert_webp(path: Path) -> Path:
    """WebP -> PNG (ou JPEG) via ffmpeg. En cas d'échec, on garde le WebP d'origine."""
    if WEBP_TO not in ("png", "jpg"):
        return path
    out = path.with_suffix("." + WEBP_TO)
    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-i", str(path), str(out)]
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    except (OSError, subprocess.TimeoutExpired) as exc:
        log(f"CONVERT FAIL {path.name} {exc}")
        return path
    if res.returncode != 0 or not out.is_file() or out.stat().st_size == 0:
        log(f"CONVERT FAIL {path.name} {res.stderr.strip()[-200:]}")
        out.unlink(missing_ok=True)
        return path
    path.unlink(missing_ok=True)
    log(f"CONVERT {path.name} -> {out.name}")
    return out


def worker() -> None:
    while True:
        with QLOCK:
            job = QUEUE.pop(0) if QUEUE else None
        if not job:
            time.sleep(0.5)
            continue
        url, tag = job
        STATS["running"] = url
        stamp = time.strftime("%Y%m%d-%H%M%S")
        prefix = f"{stamp}-{tag}"
        tmp = DIR / ".tmp" / stamp
        tmp.mkdir(parents=True, exist_ok=True)
        cmd = [GDL, "--no-mtime", "--directory", str(tmp), url]
        if COOKIES and "instagram.com" in url and Path(COOKIES).exists():
            cmd[1:1] = ["--cookies", COOKIES]
        log(f"fetch {url}")
        try:
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
            got = sorted([p for p in tmp.rglob("*") if p.is_file()])
            if res.returncode != 0 or not got:
                STATS["fail"] += 1
                reason = res.stderr.strip()[-400:] or "no media returned"
                log(f"FAIL rc={res.returncode} {reason}")
            else:
                for i, src in enumerate(got, 1):
                    if src.suffix.lower() == ".webp":
                        src = convert_webp(src)
                    dest = DIR / f"{prefix}-{i:02d}{src.suffix.lower()}"
                    src.replace(dest)
                    if XBB_URL:
                        upload_to_xbb(dest)
                STATS["ok"] += 1
                remember(url)
                log(f"OK {len(got)} file(s) {url}")
        except subprocess.TimeoutExpired:
            STATS["fail"] += 1
            log("FAIL timeout " + url)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
            STATS["running"] = None


PAGE = """<!doctype html><meta charset=utf-8><title>memes</title>
<meta name=viewport content="width=device-width,initial-scale=1">
<style>
body{{font:15px/1.4 system-ui,sans-serif;margin:0;padding:16px;background:#111;color:#eee}}
h1{{font-size:18px;margin:0 0 12px}} .g{{display:grid;gap:10px;grid-template-columns:repeat(auto-fill,minmax(180px,1fr))}}
a{{color:#8cf}} figure{{margin:0}} img{{width:100%;border-radius:8px;display:block;background:#222}}
figcaption{{font-size:11px;color:#888;word-break:break-all}}
</style>
<h1>{n} files &middot; {ok} ok / {fail} failed</h1>
<div class=g>{items}</div>"""


class H(BaseHTTPRequestHandler):
    server_version = "memedl/1.0"

    def _send(self, code: int, body: bytes, ctype="text/plain; charset=utf-8") -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self) -> None:
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "X-Token, Content-Type")
        self.send_header("Access-Control-Allow-Methods", "POST, GET, OPTIONS")
        self.end_headers()

    def _auth_ok(self) -> bool:
        u = urlparse(self.path)
        q = parse_qs(u.query)
        if q.get("token", [""])[0] == TOKEN:
            return True
        return self.headers.get("X-Token") == TOKEN

    def do_GET(self) -> None:
        u = urlparse(self.path)
        if u.path == "/healthz":
            return self._send(200, json.dumps({"queue": len(QUEUE), **STATS}).encode())
        if not self._auth_ok():
            return self._send(403, b"forbidden\n")
        if u.path == "/":
            files = sorted([p for p in DIR.iterdir()
                            if p.is_file() and not p.name.startswith(".")], reverse=True)[:300]
            share = load_links()
            items = ""
            for p in files:
                fq = f"/f/{quote(p.name)}?token={TOKEN}"
                extra = (f'<br><a href="{share[p.name]}" target=_blank>share ↗</a>'
                         if p.name in share else "")
                items += (f'<figure><a href="{fq}"><img loading=lazy src="{fq}"></a>'
                          f"<figcaption>{p.name}{extra}</figcaption></figure>")
            return self._send(200, PAGE.format(n=len(files), items=items,
                                               ok=STATS["ok"], fail=STATS["fail"]).encode(),
                              "text/html; charset=utf-8")
        if u.path.startswith("/f/"):
            name = unquote(u.path[3:])
            target = (DIR / name).resolve()
            if target.parent != DIR.resolve() or not target.is_file():
                return self._send(404, b"not found\n")
            ctype = "image/gif" if name.lower().endswith(".gif") else "image/jpeg"
            if name.lower().endswith((".mp4", ".webm")):
                ctype = "video/mp4"
            elif name.lower().endswith(".png"):
                ctype = "image/png"
            elif name.lower().endswith(".webp"):
                ctype = "image/webp"
            return self._send(200, target.read_bytes(), ctype)
        self._send(404, b"not found\n")

    def do_POST(self) -> None:
        if not self._auth_ok():
            return self._send(403, b"forbidden\n")
        n = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(n).decode("utf-8", "replace").strip()
        url = ""
        ctype = (self.headers.get("Content-Type") or "").lower()
        if "json" in ctype:
            try:
                url = (json.loads(raw).get("url") or "").strip()
            except json.JSONDecodeError:
                url = ""
        elif "form" in ctype:
            url = (parse_qs(raw).get("url", [""])[0] or "").strip()
        else:
            url = raw
        if not url:
            m = re.search(r"https?://\S+", raw)
            url = m.group(0) if m else ""
        if not re.match(r"^https?://", url):
            return self._send(400, b"need a http(s) url\n")
        if already_seen(url):
            return self._send(200, b"already have it\n")
        with QLOCK:
            QUEUE.append((url, slug(url)))
        log(f"queued {url}")
        self._send(202, b"queued\n")

    def log_message(self, fmt, *args):
        pass


if __name__ == "__main__":
    log(f"memedl on http://{BIND}:{PORT} dir={DIR} token={TOKEN}")
    if not Path(GDL).exists():
        log(f"WARNING gallery-dl not found at {GDL}")
    threading.Thread(target=worker, daemon=True).start()
    ThreadingHTTPServer((BIND, PORT), H).serve_forever()
