#!/usr/bin/env python3
"""Transcribe un audio público con faster-whisper y deja el resultado en out/.

Uso (lo rellena el workflow desde la carga firmada):
  AUDIO_URL   URL http/https pública del audio o vídeo
  MODEL       tiny|base|small|medium|large-v3 (por defecto small)
  LANGUAGE    código ISO-639-1 opcional; sin él se detecta

Salidas:
  out/transcript.txt   texto plano
  out/transcript.json  segmentos con inicio, fin, texto y confianza

Límite declarado: se descarga el fichero entero a disco antes de transcribir, así
que un audio de muchas horas necesita tanto disco como su tamaño. El runner tiene
14 GB libres; por encima de eso, este workflow no sirve.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.parse
import urllib.request

ALLOWED_MODELS = {"tiny", "tiny.en", "base", "base.en", "small", "small.en",
                  "medium", "medium.en", "large-v3", "distil-large-v3"}
MAX_BYTES = 2 * 1024 * 1024 * 1024


def check_url(url: str) -> str:
    """Sólo http/https, sin credenciales embebidas. SSRF mínimo: rechaza hosts privados."""
    import ipaddress
    import socket

    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise SystemExit("transcribe: URL no válida (sólo http/https)")
    if parsed.username or parsed.password:
        raise SystemExit("transcribe: la URL no puede llevar credenciales")
    try:
        infos = socket.getaddrinfo(parsed.hostname, None)
    except OSError as exc:
        raise SystemExit(f"transcribe: no se pudo resolver el host ({exc})") from exc
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
            raise SystemExit("transcribe: la URL apunta a una dirección no pública")
    return url


def main() -> int:
    url = os.environ.get("AUDIO_URL", "").strip()
    if not url:
        print("transcribe: falta audio_url", file=sys.stderr)
        return 2
    model_name = (os.environ.get("MODEL") or "small").strip()
    if model_name not in ALLOWED_MODELS:
        print(f"transcribe: modelo no permitido {model_name!r}", file=sys.stderr)
        return 2
    language = (os.environ.get("LANGUAGE") or "").strip() or None

    check_url(url)
    os.makedirs("out", exist_ok=True)
    suffix = os.path.splitext(urllib.parse.urlparse(url).path)[1] or ".bin"
    local = os.path.join("out", f"input{suffix}")

    with urllib.request.urlopen(url, timeout=120) as response:
        length = int(response.headers.get("Content-Length") or 0)
        if length > MAX_BYTES:
            print(f"transcribe: el fichero declara {length} bytes; el tope es {MAX_BYTES}",
                  file=sys.stderr)
            return 3
        with open(local, "wb") as handle:
            while chunk := response.read(1 << 20):
                handle.write(chunk)

    from faster_whisper import WhisperModel

    model = WhisperModel(model_name, device="cpu", compute_type="int8")
    segments, info = model.transcribe(local, language=language, vad_filter=True)

    rows = [{"start": round(s.start, 2), "end": round(s.end, 2),
             "text": s.text.strip(), "avg_logprob": round(s.avg_logprob, 4)}
            for s in segments]
    with open("out/transcript.json", "w", encoding="utf-8") as handle:
        json.dump({"model": model_name, "language": info.language,
                   "language_probability": round(info.language_probability, 4),
                   "duration_s": round(info.duration, 2), "segments": rows},
                  handle, ensure_ascii=False, indent=2)
    with open("out/transcript.txt", "w", encoding="utf-8") as handle:
        handle.write("\n".join(row["text"] for row in rows))
    os.unlink(local)
    print(f"transcribe: {len(rows)} segmentos, idioma {info.language} "
          f"({info.language_probability:.2f}), {info.duration:.1f} s de audio")
    return 0


if __name__ == "__main__":
    sys.exit(main())
