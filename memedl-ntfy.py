#!/usr/bin/env python3
"""memedl-ntfy — a link posted on an ntfy topic becomes a memedl download job.

Waits on one ntfy topic (JSON stream), pulls the first http(s) URL out of each
message, and POSTs it to the memedl hook. Stdlib only, single process.

Env:
  NT_MEMEDL_BASE            ntfy base URL           (default https://ntfy.example.com)
  NT_MEMEDL_TOPIC           topic to watch          (required)
  NT_MEMEDL_TOKEN_FILE      file with ntfy token    (optional, sent as Authorization: Bearer)
  NT_MEMEDL_HOOK            memedl hook URL         (default http://127.0.0.1:8765/hook)
  NT_MEMEDL_HOOK_TOKEN_FILE file with memedl token  (required)
  NT_MEMEDL_SINCE_FILE      resume state            (default /var/lib/memedl-ntfy/since)
  NT_MEMEDL_FIRST_LOOKBACK  where to start on cold start, ntfy since= syntax (default 10m)
"""
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

BASE = (os.environ.get("NT_MEMEDL_BASE") or "https://ntfy.example.com").rstrip("/")
TOPIC = (os.environ.get("NT_MEMEDL_TOPIC") or "").strip()
TOKEN_FILE = os.environ.get("NT_MEMEDL_TOKEN_FILE", "")
HOOK = os.environ.get("NT_MEMEDL_HOOK") or "http://127.0.0.1:8765/hook"
HOOK_TOKEN_FILE = os.environ.get("NT_MEMEDL_HOOK_TOKEN_FILE", "")
SINCE_FILE = Path(os.environ.get("NT_MEMEDL_SINCE_FILE") or "/var/lib/memedl-ntfy/since")
FIRST_LOOKBACK = os.environ.get("NT_MEMEDL_FIRST_LOOKBACK", "10m")
URL_RE = re.compile(r"https?://[^\s<>\"']+")
RETRIES = 0


def log(msg: str) -> None:
    print(time.strftime("%Y-%m-%d %H:%M:%S ") + msg, flush=True)


def read_secret(path: str) -> str:
    try:
        return Path(path).read_text().strip() if path else ""
    except OSError:
        return ""


def since() -> str:
    try:
        v = SINCE_FILE.read_text().strip()
        return v or FIRST_LOOKBACK
    except OSError:
        return FIRST_LOOKBACK


def remember(msg_id: str) -> None:
    if not msg_id:
        return
    try:
        SINCE_FILE.parent.mkdir(parents=True, exist_ok=True)
        SINCE_FILE.write_text(msg_id + "\n")
    except OSError as exc:
        log(f"state write failed: {exc}")


def forward(url: str) -> str:
    """POST the link to memedl *locally* only — never publish to the internet."""
    token = read_secret(HOOK_TOKEN_FILE)
    if not token:
        return "no hook token configured"
    req = urllib.request.Request(
        HOOK, data=url.encode(), method="POST",
        headers={"Content-Type": "text/plain", "X-Token": token,
                 "User-Agent": "memedl-ntfy/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return f"{resp.status} {resp.read().decode('utf-8', 'replace').strip()}"
    except urllib.error.HTTPError as exc:
        return f"http {exc.code} {exc.read()[:120]!r}"
    except Exception as exc:  # noqa: BLE001 - network layer
        return f"error {exc}"


def handle(event: dict) -> None:
    if event.get("event") != "message":
        return
    text = event.get("message") or ""
    found = URL_RE.findall(text)
    log(f"msg {event.get('id', '?')} {len(found)} link(s)")
    for url in found:
        url = url.rstrip(".,;)")
        log(f"-> {url} : {forward(url)}")
    remember(event.get("id", ""))


def stream() -> None:
    """Long-lived GET on /<topic>/json; each line is one JSON event."""
    url = f"{BASE}/{TOPIC}/json?since={since()}&poll=0"
    headers = {"User-Agent": "memedl-ntfy/1.0"}
    token = read_secret(TOKEN_FILE)
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=600) as resp:
        log(f"listening {BASE}/{TOPIC} since={since()}")
        for raw in resp:
            line = raw.decode("utf-8", "replace").strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if event.get("event") == "open":
                log("stream open")
            handle(event)


def main() -> int:
    if not TOPIC:
        log("NT_MEMEDL_TOPIC is required")
        return 2
    if not HOOK_TOKEN_FILE:
        log("NT_MEMEDL_HOOK_TOKEN_FILE is required")
        return 2
    global RETRIES
    warned = False
    while True:
        # A topic privé refuse la lecture sans jeton (403). Sans ce garde-fou, un
        # déploiement sans jeton encore créé boucle sur des 403 toutes les
        # quelques secondes ; ici on attend calmement le secret.
        if TOKEN_FILE and not read_secret(TOKEN_FILE):
            if not warned:
                log(f"jeton ntfy absent ({TOKEN_FILE}) — en attente, rien n'est écouté")
                warned = True
            time.sleep(60)
            continue
        warned = False
        try:
            stream()
            RETRIES = 0
        except KeyboardInterrupt:
            return 0
        except Exception as exc:  # noqa: BLE001 - reconnect loop
            RETRIES += 1
            wait = min(60, 2 ** min(RETRIES, 6))
            log(f"stream lost ({exc}); retry in {wait}s")
            time.sleep(wait)


if __name__ == "__main__":
    sys.exit(main())
