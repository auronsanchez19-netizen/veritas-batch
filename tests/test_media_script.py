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
