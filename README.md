# veritas-batch

Músculo de cómputo de Véritas en GitHub Actions. **Repo público a propósito**: los
minutos de Actions son ilimitados sólo en repos públicos, y eso es lo único que
ningún otro carril ofrece (16 GB de RAM y 6 h por job, frente a 512 MB y 60 s).

## Regla que sostiene todo lo demás

**Aquí no vive lógica del agente ni un solo secreto del producto.** Lo que hay son
pasos y workflows que ejecutan una tarea que llega de fuera. El runner recibe una
carga, la procesa y devuelve un resultado. Nada más.

| Qué | Cómo |
| --- | --- |
| Disparo | **sólo** `repository_dispatch` y `workflow_dispatch`. Nunca `pull_request_target`, ni `issue_comment`, ni `push` de ramas de terceros. |
| Acciones | fijadas por **SHA completo**, no por etiqueta (una etiqueta se puede mover). |
| Permisos | `permissions: contents: read` en todos los jobs; ninguno escribe en el repo. |
| Firma | `X-Veritas-Signature: sha256=<hmac>` sobre el cuerpo **crudo** + `X-Veritas-Nonce` de un solo uso. Se verifica **antes** de leer el JSON. |
| Secretos | uno solo: `VERITAS_DISPATCH_SECRET`. Los proveedores (AssemblyAI, etc.) **no** entran aquí. |
| Rama por defecto | `main`. |

## Workflows

| Workflow | Qué hace | Entrada | Salida |
| --- | --- | --- | --- |
| `vm.yml` | **VM de propósito general**: comandos en un runner limpio con el stack de datos ya instalado. Es la pieza para pruebas, respaldo del sandbox y verificación. | `commands[]`, `timeout_s`, `packages[]` | artefacto `vm-result` (stdout/stderr/exit por comando) |
| `transcribe.yml` | `faster-whisper`, modelo configurable | `audio_url`, `model` | artefacto con transcripción |
| `documents.yml` | `docling` + `marker`: PDF → Markdown/tablas | `document_url` | artefacto con Markdown + JSON |
| `browser.yml` | Chromium real para lo que no cabe en 512 MB | `url`, `script` | artefacto con captura + HTML |

`vm.yml` es el que se usa como **tool permanente**: el agente pide «corre esto en
una máquina de 16 GB» y recibe stdout, stderr, código de salida y artefactos.

## Cómo se dispara

```bash
BODY='{"event_type":"veritas-vm","client_payload":{"trace_id":"abc123","commands":["python3 -c \"import numpy;print(numpy.__version__)\""],"timeout_s":300}}'
SIG=$(printf '%s' "$BODY" | openssl dgst -sha256 -hmac "$VERITAS_DISPATCH_SECRET" | awk '{print $2}')
curl -s -X POST https://api.github.com/repos/auronsanchez19-netizen/veritas-batch/dispatches \
  -H "Authorization: Bearer $GH_TOKEN_BATCH" \
  -H "Accept: application/vnd.github+json" \
  -H "Content-Type: application/json" \
  -H "X-Veritas-Signature: sha256=$SIG" \
  -H "X-Veritas-Nonce: $(uuidgen)" \
  -d "$BODY"
```

`202 Accepted` significa **encolado**, no hecho. El resultado se recoge por el
artefacto del run (`GET /repos/{owner}/{repo}/actions/runs/{id}/artifacts`).

## Límites que no se pueden sortear

* **Runners efímeros**: cada job empieza de cero. Sin estado entre ejecuciones; lo
  que haya que conservar se devuelve como artefacto.
* **Artefactos**: 500 ficheros y 2 GB por artefacto en el plan Free.
* **Cola**: no es un servicio interactivo. Para respuestas en segundos, este no es
  el carril.
* **Repo público = todo lo ejecutado aquí es visible.** Nunca datos personales,
  credenciales ni material sensible en `commands` ni en artefactos.
* **No es un sandbox de seguridad**: el código que corre tiene red y disco sin
  límite por job. Sirve para código del agente; **no** para código de terceros
  hostil.
