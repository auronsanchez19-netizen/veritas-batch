#!/usr/bin/env python3
"""Despacha un job a veritas-batch, firmado, y opcionalmente espera el resultado.

Lo usa la tool `github_vm_run` del agente. También funciona a mano para depurar.

Uso:
  python3 scripts/dispatch.py vm --command "python3 -c 'print(1)'" [--wait]
  python3 scripts/dispatch.py transcribe --url https://... --model small
  python3 scripts/dispatch.py documents --url https://...pdf
  python3 scripts/dispatch.py browser --url https://...

Variables:
  GH_TOKEN_BATCH              token con scope repo (nunca en el repo)
  VERITAS_DISPATCH_SECRET     secreto compartido con el workflow (32+ caracteres)
  VERITAS_BATCH_REPO          por defecto auronsanchez19-netizen/veritas-batch

La firma se calcula sobre el JSON **canónico** del cuerpo que se envía dentro de
`client_payload.body`, igual que lo verifica `verify_signature.py` (claves
ordenadas, separadores compactos).
"""
from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import secrets
import sys
import time
import urllib.error
import urllib.request

API = "https://api.github.com"
DEFAULT_REPO = os.environ.get("VERITAS_BATCH_REPO", "auronsanchez19-netizen/veritas-batch")
EVENT_TYPES = {
    "vm": "veritas-vm",
    "transcribe": "veritas-transcribe",
    "documents": "veritas-documents",
    "browser": "veritas-browser",
}


def canonical(payload: dict) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False).encode("utf-8")


def request(path: str, *, method: str = "GET", token: str, body: dict | None = None,
            accept: str = "application/vnd.github+json") -> dict:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(f"{API}{path}", data=data, method=method,
                                 headers={"Authorization": f"Bearer {token}",
                                          "Accept": accept,
                                          "Content-Type": "application/json",
                                          "X-GitHub-Api-Version": "2022-11-28"})
    with urllib.request.urlopen(req, timeout=60) as response:
        raw = response.read()
        return json.loads(raw) if raw else {}


def build_payload(kind: str, body: dict) -> tuple[dict, str]:
    secret = os.environ.get("VERITAS_DISPATCH_SECRET") or ""
    if len(secret) < 32:
        raise SystemExit("dispatch: falta VERITAS_DISPATCH_SECRET (32+ caracteres)")
    nonce = secrets.token_hex(16)
    signature = "sha256=" + hmac.new(secret.encode(), canonical(body),
                                     hashlib.sha256).hexdigest()
    return {"event_type": EVENT_TYPES[kind],
            "client_payload": {"body": body, "nonce": nonce, "sig": signature}}, nonce


def dispatch(kind: str, body: dict, token: str, repo: str) -> dict:
    payload, nonce = build_payload(kind, body)
    request(f"/repos/{repo}/dispatches", method="POST", token=token, body=payload)
    return {"dispatched": True, "event_type": payload["event_type"], "nonce": nonce}


def wait_for_run(repo: str, token: str, *, since: float, timeout_s: int = 900) -> dict:
    """Espera a que aparezca un run del event_type y termina. Devuelve su estado."""
    deadline = time.time() + timeout_s
    run = None
    while time.time() < deadline:
        runs = request(f"/repos/{repo}/actions/runs?event=repository_dispatch&per_page=20",
                       token=token).get("workflow_runs", [])
        for candidate in runs:
            created = time.mktime(time.strptime(candidate["created_at"],
                                                "%Y-%m-%dT%H:%M:%SZ"))
            if created >= since - 5 and candidate["status"] != "completed":
                run = candidate
                break
            if created >= since - 5 and run is None:
                run = candidate
        if run and run["status"] == "completed":
            return {"id": run["id"], "conclusion": run["conclusion"],
                    "url": run["html_url"], "name": run["name"]}
        time.sleep(10)
    return {"id": run["id"] if run else None, "conclusion": "timeout",
            "url": run["html_url"] if run else None}


def fetch_artifact(repo: str, token: str, run_id: int, name: str) -> dict | None:
    artifacts = request(f"/repos/{repo}/actions/runs/{run_id}/artifacts",
                        token=token).get("artifacts", [])
    for artifact in artifacts:
        if artifact["name"] == name:
            return {"name": name, "size": artifact["size_in_bytes"],
                    "id": artifact["id"],
                    "download_url": artifact["archive_download_url"]}
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description="Despacha un job a veritas-batch")
    parser.add_argument("kind", choices=sorted(EVENT_TYPES))
    parser.add_argument("--command", action="append", default=[],
                        help="comando para vm (se ejecuta como argv, sin shell)")
    parser.add_argument("--packages", nargs="*", default=[])
    parser.add_argument("--url", help="audio/documento/web para transcribe/documents/browser")
    parser.add_argument("--model", default="small")
    parser.add_argument("--language", default=None)
    parser.add_argument("--timeout-s", type=int, default=300)
    parser.add_argument("--wait", action="store_true")
    parser.add_argument("--repo", default=DEFAULT_REPO)
    args = parser.parse_args()

    token = os.environ.get("GH_TOKEN_BATCH") or ""
    if len(token) < 20:
        raise SystemExit("dispatch: falta GH_TOKEN_BATCH")

    body: dict = {}
    if args.kind == "vm":
        import shlex
        commands = [shlex.split(c) if isinstance(c, str) else c for c in args.command]
        if not commands:
            raise SystemExit("dispatch: vm necesita al menos un --command")
        body = {"commands": commands, "timeout_s": args.timeout_s,
                "packages": args.packages, "trace_id": secrets.token_hex(8)}
    else:
        if not args.url:
            raise SystemExit(f"dispatch: {args.kind} necesita --url")
        body = {"trace_id": secrets.token_hex(8)}
        if args.kind == "transcribe":
            body.update({"audio_url": args.url, "model": args.model,
                         "language": args.language})
        elif args.kind == "documents":
            body.update({"document_url": args.url})
        else:
            body.update({"url": args.url, "full_page": True})

    started = time.time()
    result = dispatch(args.kind, body, token, args.repo)
    print(json.dumps(result))

    if args.wait:
        outcome = wait_for_run(args.repo, token, since=started,
                               timeout_s=max(120, args.timeout_s + 600))
        artifact = (fetch_artifact(args.repo, token, outcome["id"], "vm-result")
                    if outcome.get("id") and args.kind == "vm" else None)
        print(json.dumps({"run": outcome, "artifact": artifact}, indent=2))
        if outcome.get("conclusion") != "success":
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
