#!/usr/bin/env python3
"""Verifica la firma HMAC y el nonce de un despacho antes de leer la carga.

Se ejecuta como primer paso de cada workflow. Si algo no cuadra, sale con código
distinto de cero y el job no llega a ejecutar nada del llamante.

Reglas:
  * HMAC-SHA256 sobre el **cuerpo crudo** del `client_payload` serializado de forma
    canónica (JSON con claves ordenadas y sin espacios), porque GitHub re-serializa
    el payload y no hay acceso al cuerpo original del POST.
  * Nonce obligatorio, 16–128 caracteres alfanuméricos o guiones.
  * Comparación en tiempo constante.
  * Sin `VERITAS_DISPATCH_SECRET` no se valida nada: se sale con error (fail-closed).
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import sys

NONCE_RE = re.compile(r"^[A-Za-z0-9-]{16,128}$")
MIN_SECRET_LEN = 32


def canonical(payload: dict) -> bytes:
    """JSON canónico: claves ordenadas, separadores compactos, UTF-8."""
    return json.dumps(payload, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False).encode("utf-8")


def main() -> int:
    secret = os.environ.get("VERITAS_DISPATCH_SECRET") or ""
    if len(secret) < MIN_SECRET_LEN:
        print(f"veritas-firma: ERROR falta VERITAS_DISPATCH_SECRET (o mide menos de "
              f"{MIN_SECRET_LEN} caracteres); no se valida nada", file=sys.stderr)
        return 2

    signature = os.environ.get("SIGNATURE", "")
    nonce = os.environ.get("NONCE", "")
    raw = os.environ.get("PAYLOAD", "")

    if not signature:
        print("veritas-firma: ERROR sin cabecera de firma (el evento no trae "
              "X-Veritas-Signature)", file=sys.stderr)
        return 3
    if not NONCE_RE.match(nonce):
        print("veritas-firma: ERROR nonce ausente o mal formado", file=sys.stderr)
        return 4
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        print(f"veritas-firma: ERROR payload no es JSON ({exc})", file=sys.stderr)
        return 5
    if not isinstance(payload, dict):
        print("veritas-firma: ERROR el payload no es un objeto", file=sys.stderr)
        return 6

    expected = "sha256=" + hmac.new(secret.encode(), canonical(payload),
                                    hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, signature):
        print("veritas-firma: ERROR firma inválida", file=sys.stderr)
        return 7

    print(f"veritas-firma: OK (nonce {nonce[:8]}…, {len(payload)} claves)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
