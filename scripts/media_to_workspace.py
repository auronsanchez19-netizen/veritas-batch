#!/usr/bin/env python3
"""Descarga un recurso con enlace firmado y devuelve el texto al Worker.

Este script sustituye al patrón «descargar y subir un artefacto». El repo es
público: un artefacto con material del usuario lo puede bajar cualquiera con una
cuenta de GitHub, y los logs también son públicos. Por eso:

  * se descarga por un enlace **firmado y de un solo uso** que caduca en minutos;
  * el texto se devuelve por **POST firmado** al endpoint interno del Worker;
  * no se imprime la URL ni el texto, y no se sube ningún artefacto;
  * el archivo de entrada se borra del disco al terminar.

Variables (las rellena el workflow desde la carga firmada):
  MEDIA_URL        enlace firmado de descarga (nunca se imprime)
  RESULT_URL       endpoint interno que recibe el texto, con su token de capacidad
  JOB              audio | video | documents
  FILENAME         nombre original del archivo, para nombrar el resultado
  USER_ID          dueño del workspace al que se devuelve el texto
  MODEL            modelo de whisper (solo audio/video)
  LANGUAGE         idioma forzado, opcional

  Este script NO guarda ni necesita ningún secreto: la autorización para devolver
  el texto va dentro de `RESULT_URL`, en un token HMAC que el Worker emite para
  ese usuario y ese archivo, y que caduca en 6 horas.

Límites declarados: 1 GB de descarga, 30 min de audio para el modo `small` en
4 vCPU (por encima, el job se corta por tiempo y se devuelve lo que haya), y la
devolución se recorta en el Worker a 2 000 000 de caracteres.
"""

from __future__ import annotations

from typing import NoReturn



import ipaddress
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import urllib.parse
import urllib.request

MAX_BYTES = 1024 * 1024 * 1024


def die(message: str, code: int = 2) -> "NoReturn":
    """Falla con código explícito: 2 = entrada inválida, 3 = extracción vacía."""
    print(message, file=sys.stderr)
    raise SystemExit(code)


def check_url(url: str) -> str:
    """Sólo http/https, sin credenciales embebidas y sin hosts privados."""
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise SystemExit("media: URL no válida (sólo http/https)")
    if parsed.username or parsed.password:
        die("media: la URL no puede llevar credenciales")
    try:
        infos = socket.getaddrinfo(parsed.hostname, None)
    except OSError as exc:
        raise SystemExit(f"media: no se pudo resolver el host ({exc})") from exc
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
            die("media: la URL apunta a una dirección no pública")
    return url


def download(url: str, directory: str) -> str:
    check_url(url)
    suffix = os.path.splitext(urllib.parse.urlparse(url).path)[1] or ".bin"
    local = os.path.join(directory, f"input{suffix}")
    with urllib.request.urlopen(url, timeout=120) as response:
        length = int(response.headers.get("Content-Length") or 0)
        if length > MAX_BYTES:
            die(f"media: el fichero declara {length} bytes; el tope es {MAX_BYTES}")
        written = 0
        with open(local, "wb") as handle:
            while chunk := response.read(1 << 20):
                written += len(chunk)
                if written > MAX_BYTES:
                    die("media: descarga supera el tope")
                handle.write(chunk)
    print(f"media: descargados {written} bytes")  # nunca la URL
    return local


def to_audio(path: str, directory: str) -> str:
    """Vídeo → pista de audio mono 16 kHz, que es lo que whisper necesita."""
    target = os.path.join(directory, "audio.wav")
    result = subprocess.run(["ffmpeg", "-nostdin", "-y", "-i", path, "-vn",
                             "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", target],
                            capture_output=True, text=True)
    if result.returncode != 0:
        raise SystemExit(f"media: ffmpeg falló ({result.stderr.strip()[:300]})")
    return target


def transcribe(path: str, model_name: str, language: str | None) -> tuple[str, str]:
    from faster_whisper import WhisperModel

    model = WhisperModel(model_name, device="cpu", compute_type="int8")
    segments, info = model.transcribe(path, language=language, vad_filter=True)
    lines = [segment.text.strip() for segment in segments if segment.text.strip()]
    note = f"idioma {info.language} ({info.language_probability:.2f}), {info.duration:.1f} s de audio"
    return "\n".join(lines), note


def pdf_to_markdown(path: str) -> tuple[str, str]:
    from docling.document_converter import DocumentConverter

    result = DocumentConverter().convert(path)
    markdown = result.document.export_to_markdown()
    pages = len(getattr(result.document, "pages", {}) or {})
    return markdown, f"docling, {pages} páginas"


def deliver(result_url: str, payload: dict) -> None:
    """Devuelve el texto al Worker. La URL firmada es toda la autorización."""
    check_url(result_url)
    body = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    request = urllib.request.Request(result_url, data=body, method="POST", headers={
        "Content-Type": "application/json",
        "User-Agent": "veritas-batch/1.0",
    })
    with urllib.request.urlopen(request, timeout=60) as response:
        detail = response.read(400).decode(errors="replace")
        print(f"media: devolución {response.status} {detail}")


def main() -> int:
    url = (os.environ.get("MEDIA_URL") or "").strip()
    result_url = (os.environ.get("RESULT_URL") or "").strip()
    job = (os.environ.get("JOB") or "audio").strip()
    filename = (os.environ.get("FILENAME") or "archivo").strip()
    user_id = (os.environ.get("USER_ID") or "").strip()
    model_name = (os.environ.get("MODEL") or "small").strip()
    language = (os.environ.get("LANGUAGE") or "").strip() or None

    missing = [name for name, value in (("MEDIA_URL", url), ("RESULT_URL", result_url),
                                        ("USER_ID", user_id)) if not value]
    if missing:
        print(f"media: faltan variables {', '.join(missing)}", file=sys.stderr)
        return 2
    if job not in ("audio", "video", "documents"):
        print(f"media: job no soportado {job!r}", file=sys.stderr)
        return 2

    directory = tempfile.mkdtemp(prefix="veritas-media-")
    try:
        local = download(url, directory)
        if job == "documents":
            text, note = pdf_to_markdown(local)
        else:
            source = local if job == "audio" else to_audio(local, directory)
            text, note = transcribe(source, model_name, language)
        if not text.strip():
            print("media: la extracción no produjo texto", file=sys.stderr)
            return 3
        deliver(result_url, {
            "userId": user_id, "filename": filename, "job": job,
            "text": text, "note": note,
        })
        print(f"media: {len(text)} caracteres devueltos")
        return 0
    finally:
        shutil.rmtree(directory, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
