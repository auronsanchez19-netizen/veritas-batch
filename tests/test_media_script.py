#!/usr/bin/env python3
"""Pruebas del runner sin red ni dependencias pesadas."""
import os
import subprocess
import sys
import unittest

SCRIPT = os.path.join(os.path.dirname(__file__), "..", "scripts", "media_to_workspace.py")


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
