#!/usr/bin/env python3
"""PDF público -> Markdown + JSON con docling.

Salidas en out/: document.md (contenido), document.json (estructura), summary.json
(páginas, tablas, imágenes extraídas).

Límite: docling descarga sus modelos la primera vez (cientos de MB) en cada job,
porque el runner es efímero. Un run corto puede tardar más en preparar el modelo
que en convertir el documento.
"""
from __future__ import annotations

import json
import os
import sys
import urllib.parse
import urllib.request

MAX_BYTES = 500 * 1024 * 1024


def main() -> int:
    url = (os.environ.get("DOCUMENT_URL") or "").strip()
    if not url:
        print("parse_document: falta document_url", file=sys.stderr)
        return 2
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        print("parse_document: URL no válida (sólo http/https)", file=sys.stderr)
        return 2

    os.makedirs("out", exist_ok=True)
    local = "out/input.pdf"
    with urllib.request.urlopen(url, timeout=120) as response:
        length = int(response.headers.get("Content-Length") or 0)
        if length > MAX_BYTES:
            print(f"parse_document: {length} bytes; el tope es {MAX_BYTES}", file=sys.stderr)
            return 3
        with open(local, "wb") as handle:
            while chunk := response.read(1 << 20):
                handle.write(chunk)

    from docling.document_converter import DocumentConverter

    result = DocumentConverter().convert(local)
    markdown = result.document.export_to_markdown()
    with open("out/document.md", "w", encoding="utf-8") as handle:
        handle.write(markdown)
    with open("out/document.json", "w", encoding="utf-8") as handle:
        json.dump(result.document.export_to_dict(), handle, ensure_ascii=False, indent=2)

    pages = getattr(result.document, "pages", {}) or {}
    tables = len(markdown.count("|---")) if markdown else 0
    with open("out/summary.json", "w", encoding="utf-8") as handle:
        json.dump({"pages": len(pages), "markdown_chars": len(markdown),
                   "tables_detected": tables}, handle, indent=2)
    os.unlink(local)
    print(f"parse_document: {len(pages)} páginas, {len(markdown)} caracteres de markdown")
    return 0


if __name__ == "__main__":
    sys.exit(main())
