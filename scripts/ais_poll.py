#!/usr/bin/env python3
"""Sondeo AIS desde un runner de GitHub Actions (IP propia).

Render comparte IP de salida y aisstream limita a 3 conexiones por IP, así que
desde allí el handshake se rechaza. Aquí no: el runner tiene IP propia.

No imprime ni publica la clave. El resultado va por HTTPS al ingest de Véritas;
en el repo sólo queda el código.

Variables de entorno:
  AISSTREAM_API_KEY   secreto de Actions
  AIS_INGEST_URL      p. ej. https://veritas-ia-agentic-api.onrender.com/internal/ais/snapshot
  AIS_INGEST_SECRET   secreto de Actions
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import time

import websockets

URL = "wss://stream.aisstream.io/v0/stream"
# Cajas densas y repartidas: el chorro de aisstream devuelve lo que emiten esos
# receptores, así que interesa muestrear mucho tráfico en poco tiempo.
BOXES = [
    ("rotterdam", [51.6, 2.2, 52.4, 4.6]),
    ("singapore", [1.1, 103.5, 1.5, 104.1]),
    ("gibraltar", [35.8, -5.8, 36.3, -5.0]),
    ("houston", [29.2, -95.2, 29.9, -94.6]),
    ("suez", [29.6, 32.2, 30.2, 32.7]),
    ("panama", [8.6, -79.9, 9.4, -79.2]),
]
SECONDS_PER_BOX = 10
TYPES = ["PositionReport", "StandardClassBPositionReport", "ExtendedClassBPositionReport"]


async def sample(box: list[float], seconds: int, key: str) -> list[dict]:
    seen: dict[str, dict] = {}
    subscription = {
        "APIKey": key,
        "BoundingBoxes": [[[box[0], box[1]], [box[2], box[3]]]],
        "FilterMessageTypes": TYPES,
    }
    # Escribir la suscripción dentro del `async with` evita depender de un
    # temporizador: al salir, websockets cierra el socket y libera la conexión
    # (el servicio sólo permite 3 por IP).
    async with websockets.connect(URL, open_timeout=20, close_timeout=5, max_size=2**22) as socket:
        await socket.send(json.dumps(subscription))
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            try:
                raw = await asyncio.wait_for(socket.recv(), timeout=max(0.1, deadline - time.monotonic()))
            except asyncio.TimeoutError:
                break
            try:
                envelope = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if envelope.get("MessageType") == "SubscriptionConfirmation":
                continue
            meta = envelope.get("MetaData") or {}
            body = (envelope.get("Message") or {}).get(envelope.get("MessageType") or "") or {}
            mmsi = str(meta.get("MMSI") or "").strip()
            lat, lon = meta.get("latitude"), meta.get("longitude")
            if len(mmsi) != 9 or not mmsi.isdigit() or not isinstance(lat, (int, float)) or not isinstance(lon, (int, float)):
                continue
            if abs(lat) > 90 or abs(lon) > 180:
                continue
            seen[mmsi] = {
                "mmsi": mmsi,
                "name": str(meta.get("ShipName") or "").strip()[:200],
                "latitude": float(lat),
                "longitude": float(lon),
                "speedKn": body.get("Sog") if isinstance(body.get("Sog"), (int, float)) else None,
                "courseDeg": body.get("Cog") if isinstance(body.get("Cog"), (int, float)) else None,
                "headingDeg": body.get("TrueHeading") if isinstance(body.get("TrueHeading"), (int, float)) else None,
                "navStatus": f"code {body['NavigationalStatus']}" if isinstance(body.get("NavigationalStatus"), int) else "",
                "observedAt": time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime()),
            }
    return list(seen.values())


async def main() -> int:
    key = os.environ.get("AISSTREAM_API_KEY", "")
    ingest = os.environ.get("AIS_INGEST_URL", "")
    secret = os.environ.get("AIS_INGEST_SECRET", "")
    if not key or not ingest or not secret:
        print("faltan AISSTREAM_API_KEY / AIS_INGEST_URL / AIS_INGEST_SECRET", file=sys.stderr)
        return 2
    gathered: dict[str, dict] = {}
    used: list[list[float]] = []
    for label, box in BOXES:
        try:
            found = await sample(box, SECONDS_PER_BOX, key)
        except Exception as error:  # noqa: BLE001
            print(f"{label}: sin datos ({type(error).__name__})", file=sys.stderr)
            await asyncio.sleep(3)
            continue
        print(f"{label}: {len(found)} embarcaciones")
        used.append(box)
        for vessel in found:
            gathered[vessel["mmsi"]] = vessel
        await asyncio.sleep(2)  # deja respirar al límite de conexiones
    if not gathered:
        print("sin datos en ninguna caja", file=sys.stderr)
        return 1
    payload = {
        "generatedAt": time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime()),
        "boxes": used,
        "vessels": list(gathered.values()),
    }
    body = json.dumps(payload).encode()
    import urllib.request
    request = urllib.request.Request(ingest, data=body, method="POST", headers={
        "Content-Type": "application/json",
        "X-Veritas-Ais-Ingest": secret,
        "User-Agent": "VeritasAIS-Poller/1.0",
    })
    with urllib.request.urlopen(request, timeout=45) as response:
        answer = response.read().decode()[:300]
        print("ingest:", response.status, answer)
    print(f"total {len(gathered)} embarcaciones de {len(used)} cajas")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
