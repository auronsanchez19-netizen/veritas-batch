#!/usr/bin/env python3
"""Ejecuta los comandos que llegan en la carga firmada y deja el resultado en out/.

Cada comando es una **lista** (argv), no una cadena: no se pasa por `shell=True`,
así que un `;`, un `|` o un `$(...)` dentro de un argumento es literal y no se
interpreta. Es la diferencia entre «ejecutar lo que el agente pidió» y «ejecutar
lo que el agente escribió».

Salidas:
  out/result.json   un objeto por comando: argv, exit_code, stdout, stderr, ms
  out/stdout.txt    todo el stdout concatenado, para lectura rápida
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time

MAX_COMMANDS = 10
MAX_STDOUT = 200_000
MAX_STDERR = 50_000
DEFAULT_TIMEOUT_S = 300
MAX_TIMEOUT_S = 3600


def as_argv(command) -> list[str] | None:
    """Acepta lista de strings. Una cadena suelta se rechaza a propósito."""
    if isinstance(command, list) and command and all(isinstance(x, str) for x in command):
        return [str(x) for x in command]
    return None


def main() -> int:
    raw = os.environ.get("COMMANDS", "[]")
    try:
        commands = json.loads(raw)
    except json.JSONDecodeError:
        print("run_commands: ERROR commands no es JSON", file=sys.stderr)
        return 2
    if not isinstance(commands, list):
        print("run_commands: ERROR commands no es una lista", file=sys.stderr)
        return 2
    if not commands:
        print("run_commands: ERROR no hay comandos que ejecutar", file=sys.stderr)
        return 2
    if len(commands) > MAX_COMMANDS:
        print(f"run_commands: ERROR {len(commands)} comandos; el tope es {MAX_COMMANDS}",
              file=sys.stderr)
        return 2

    try:
        timeout_s = int(os.environ.get("TIMEOUT_S") or DEFAULT_TIMEOUT_S)
    except ValueError:
        timeout_s = DEFAULT_TIMEOUT_S
    timeout_s = max(1, min(timeout_s, MAX_TIMEOUT_S))

    os.makedirs("out", exist_ok=True)
    results: list[dict] = []
    all_stdout: list[str] = []

    for index, command in enumerate(commands):
        argv = as_argv(command)
        if argv is None:
            results.append({"argv": command, "exit_code": None,
                            "error": "comando rechazado: debe ser una lista de cadenas, "
                                     "no una cadena de shell"})
            print(f"run_commands: comando {index} rechazado (no es lista de cadenas)",
                  file=sys.stderr)
            continue
        started = time.perf_counter()
        try:
            completed = subprocess.run(argv, capture_output=True, text=True,
                                       timeout=timeout_s, check=False)
            elapsed = (time.perf_counter() - started) * 1000
            stdout = completed.stdout[:MAX_STDOUT]
            stderr = completed.stderr[:MAX_STDERR]
            code = completed.returncode
        except subprocess.TimeoutExpired:
            elapsed = (time.perf_counter() - started) * 1000
            stdout, stderr, code = "", f"timeout tras {timeout_s} s", None
        except FileNotFoundError:
            elapsed = (time.perf_counter() - started) * 1000
            stdout, stderr, code = "", f"ejecutable no encontrado: {argv[0]}", None
        except OSError as exc:
            elapsed = (time.perf_counter() - started) * 1000
            stdout, stderr, code = "", f"error del sistema: {exc}", None

        results.append({"argv": argv, "exit_code": code, "stdout": stdout,
                        "stderr": stderr, "ms": round(elapsed, 1)})
        all_stdout.append(f"$ {' '.join(argv)}\n{stdout}")
        print(f"[{index}] exit={code} {elapsed:.0f} ms -> {' '.join(argv)[:80]}")

    payload = {"count": len(results), "results": results,
               "runner": {"python": sys.version.split()[0], "timeout_s": timeout_s}}
    with open("out/result.json", "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
    with open("out/stdout.txt", "w", encoding="utf-8") as handle:
        handle.write("\n".join(all_stdout))

    # Un fallo de un comando no tumba el job: el llamante decide. Sí falla el job
    # si NINGÚN comando llegó a ejecutarse.
    if not any(r.get("exit_code") is not None for r in results):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
