#!/usr/bin/env python3
"""Pruebas del runner sin red ni dependencias pesadas."""
import json
import os
import subprocess
import sys
import unittest

SCRIPT = os.path.join(os.path.dirname(__file__), "..", "scripts", "media_to_workspace.py")


def load_module(name: str):
    """Módulo fresco desde el archivo: cada test parchea el suyo, sin fugas."""
    import importlib.util
    spec = importlib.util.spec_from_file_location(name, SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def run(env: dict) -> subprocess.CompletedProcess:
    base = {k: v for k, v in os.environ.items() if k not in ("MEDIA_URL", "RESULT_URL", "USER_ID", "JOB", "FILENAME")}
    base.update(env)
    return subprocess.run([sys.executable, SCRIPT], env=base, capture_output=True, text=True, timeout=60)


class MediaScriptTest(unittest.TestCase):
    def test_faltan_variables(self):
        result = run({})
        self.assertEqual(result.returncode, 2)
        self.assertIn("faltan variables", result.stderr)

    def test_job_no_soportado(self):
        result = run({"MEDIA_URL": "https://example.com/a.mp3", "RESULT_URL": "https://example.com/r",
                      "USER_ID": "u-1", "JOB": "raro"})
        self.assertEqual(result.returncode, 2)
        self.assertIn("job no soportado", result.stderr)

    def test_host_privado_se_rechaza(self):
        result = run({"MEDIA_URL": "http://127.0.0.1/a.mp3", "RESULT_URL": "https://example.com/r",
                      "USER_ID": "u-1", "JOB": "audio"})
        self.assertEqual(result.returncode, 2)
        self.assertIn("no pública", result.stderr)

    def test_url_con_credenciales_se_rechaza(self):
        result = run({"MEDIA_URL": "https://user:pass@example.com/a.mp3", "RESULT_URL": "https://example.com/r",
                      "USER_ID": "u-1", "JOB": "audio"})
        self.assertEqual(result.returncode, 2)
        self.assertIn("credenciales", result.stderr)


class FullRunTest(unittest.TestCase):
    """El camino completo: descarga, transcripción y devolución al Worker.

    Hasta ahora las pruebas cubrían la descarga y las validaciones; el resto se
    estrenaba en vivo, y ahí aparecieron los fallos de `requests`, `av` y del
    propio nombre `local`. Aquí se ejecuta `main()` de punta a punta con la
    transcripción sustituida por un doble: lo que se prueba es el encadenado
    (variables, descarga, texto, POST de vuelta y código de salida).
    """

    def setUp(self):
        import functools
        import http.server
        import threading
        self.recibido = []
        recibido = self.recibido
        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200)
                self.send_header('Content-Type', 'audio/wav')
                self.send_header('Content-Length', '4100')
                self.end_headers()
                self.wfile.write(b'RIFF' + b'\x00' * 4096)
            def do_POST(self):
                import traceback
                try:
                    self._post()
                except Exception:
                    traceback.print_exc()
                    self.send_error(500)

            def _post(self):
                length = int(self.headers.get('Content-Length') or 0)
                self.server.recibido.append(self.rfile.read(length))
                body = b'{"ok":true,"file":"a.wav.texto.txt","chars":17}'
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            def log_message(self, *args):
                pass
        self.server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.server.recibido = recibido
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.base = f'http://127.0.0.1:{self.server.server_address[1]}'

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        for key in ('MEDIA_URL', 'RESULT_URL', 'USER_ID', 'JOB', 'FILENAME', 'MODEL'):
            os.environ.pop(key, None)

    def test_corrida_completa_devuelve_el_texto(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location('media_full', SCRIPT)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        module.check_url = lambda url: url  # servidor local: se salta el guardián de red
        module.transcribe = lambda path, model, language: ('en Cuba hubo apagones.', 'idioma es (0.99), 7.0 s de audio')

        os.environ.update({'MEDIA_URL': f'{self.base}/a.wav', 'RESULT_URL': f'{self.base}/result',
                           'USER_ID': 'u-1', 'JOB': 'audio', 'FILENAME': 'a.wav', 'MODEL': 'small'})
        codigo = module.main()

        self.assertEqual(codigo, 0)
        self.assertEqual(len(self.recibido), 1, 'debe hacer exactamente una devolución')
        payload = json.loads(self.recibido[0])
        self.assertEqual(payload['text'], 'en Cuba hubo apagones.')
        self.assertEqual(payload['filename'], 'a.wav')
        self.assertEqual(payload['userId'], 'u-1')
        self.assertIn('7.0 s', payload['note'])

    def test_sin_texto_devuelve_la_nota_y_sale_con_0(self):
        """Un archivo sin voz no es un fallo del carril.

        Antes salía con código 3 y no devolvía nada: el 2026-11-16, con un vídeo
        de 11 MB de tono puro, el run de Actions terminó en `failure` y el
        usuario no supo por qué. Lo correcto es entregar la nota real.
        """
        module = load_module('media_vacio')
        module.check_url = lambda url: url
        module.download = lambda url, directory: os.path.join(directory, 'a.wav')
        module.transcribe = lambda path, model, language: ('   ', 'idioma es (0.10), 1.0 s de audio')

        os.environ.update({'MEDIA_URL': f'{self.base}/a.wav', 'RESULT_URL': f'{self.base}/result',
                           'USER_ID': 'u-1', 'JOB': 'audio', 'FILENAME': 'a.wav'})
        codigo = module.main()

        self.assertEqual(codigo, 0)
        self.assertEqual(len(self.recibido), 1, 'se entrega el resultado, no se calla')
        payload = json.loads(self.recibido[0])
        self.assertEqual(payload['text'], '(sin voz detectada en el archivo)')
        self.assertIn('no se detectaron segmentos de voz', payload['note'])
        self.assertIn('1.0 s', payload['note'])

    def test_video_con_voz_sigue_entregando_el_texto(self):
        """El arreglo no cambia el camino feliz."""
        module = load_module('media_con_voz')
        module.check_url = lambda url: url
        module.download = lambda url, directory: os.path.join(directory, 'v.mp4')
        module.to_audio = lambda local, directory: os.path.join(directory, 'a.wav')
        module.transcribe = lambda source, model, language: ('hola mundo', 'idioma es (0.99), 9.0 s')

        os.environ.update({'MEDIA_URL': f'{self.base}/v.mp4', 'RESULT_URL': f'{self.base}/result',
                           'USER_ID': 'u-1', 'JOB': 'video', 'FILENAME': 'v.mp4'})
        codigo = module.main()

        self.assertEqual(codigo, 0)
        payload = json.loads(self.recibido[0])
        self.assertEqual(payload['text'], 'hola mundo')
        self.assertEqual(payload['job'], 'video')

    def test_documento_sin_texto_no_dice_sin_voz(self):
        """El aviso tiene que hablar del archivo que llegó, no de voz."""
        module = load_module('media_pdf_sin_texto')
        module.check_url = lambda url: url
        module.download = lambda url, directory: os.path.join(directory, 'a.pdf')
        module.pdf_to_markdown = lambda path: ('', 'docling, 0 páginas')

        os.environ.update({'MEDIA_URL': f'{self.base}/a.pdf', 'RESULT_URL': f'{self.base}/result',
                           'USER_ID': 'u-1', 'JOB': 'documents', 'FILENAME': 'escaneo.pdf'})
        codigo = module.main()

        self.assertEqual(codigo, 0)
        payload = json.loads(self.recibido[0])
        self.assertIn('sin texto extraíble', payload['text'])
        self.assertNotIn('voz', payload['text'])

if __name__ == "__main__":
    unittest.main()


class DownloadTest(unittest.TestCase):
    """Descarga real contra un servidor local, saltándose sólo el guardián de red."""

    def setUp(self):
        import functools
        import http.server
        import threading
        import tempfile
        self.dir = tempfile.mkdtemp()
        os.makedirs(os.path.join(self.dir, 'www'), exist_ok=True)
        with open(os.path.join(self.dir, 'www', 'a.wav'), 'wb') as handle:
            handle.write(b'RIFF' + b'\x00' * 4096)
        handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=os.path.join(self.dir, 'www'))
        self.server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = f'http://127.0.0.1:{self.server.server_address[1]}/a.wav'

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()

    def test_descarga_escribe_el_archivo(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location('media', SCRIPT)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        module.check_url = lambda url: url  # sólo para el test: el servidor es local
        local = module.download(self.url, self.dir)
        with open(local, 'rb') as handle:
            self.assertEqual(handle.read(4), b'RIFF')
        module.check_url = None

    def test_404_da_mensaje_sin_url(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location('media2', SCRIPT)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        module.check_url = lambda url: url
        with self.assertRaises(SystemExit) as ctx:
            module.download(self.url.replace('a.wav', 'no-existe.wav'), self.dir)
        self.assertEqual(ctx.exception.code, 3)
