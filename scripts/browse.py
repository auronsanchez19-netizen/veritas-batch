#!/usr/bin/env python3
"""Abre una URL con Chromium y devuelve captura, HTML y texto visible.

Salidas en out/: screenshot.png, page.html, page.txt, meta.json.

Límite declarado: es un navegador real visitando un sitio real desde la IP de
GitHub (pública y conocida). Sitios que bloquean datacenter devolverán captcha o
403, y eso se devuelve tal cual: no se intenta evadir.
"""
from __future__ import annotations

import json
import os
import sys
import urllib.parse


def main() -> int:
    url = (os.environ.get("TARGET_URL") or "").strip()
    if not url:
        print("browse: falta url", file=sys.stderr)
        return 2
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        print("browse: URL no válida (sólo http/https)", file=sys.stderr)
        return 2
    full_page = (os.environ.get("FULL_PAGE") or "true").lower() != "false"

    os.makedirs("out", exist_ok=True)
    from playwright.sync_api import sync_playwright

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(args=["--no-sandbox"])
        page = browser.new_page(viewport={"width": 1440, "height": 900},
                                user_agent="VeritasAI/3.0 (+investigacion; contacto via repo)")
        response = page.goto(url, wait_until="networkidle", timeout=45_000)
        page.screenshot(path="out/screenshot.png", full_page=full_page)
        html = page.content()
        text = page.inner_text("body")
        meta = {"url": page.url, "status": response.status if response else None,
                "title": page.title(), "html_chars": len(html), "text_chars": len(text)}
        browser.close()

    with open("out/page.html", "w", encoding="utf-8") as handle:
        handle.write(html)
    with open("out/page.txt", "w", encoding="utf-8") as handle:
        handle.write(text)
    with open("out/meta.json", "w", encoding="utf-8") as handle:
        json.dump(meta, handle, ensure_ascii=False, indent=2)
    print(f"browse: {meta['status']} {meta['title'][:80]!r} ({meta['text_chars']} chars)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
