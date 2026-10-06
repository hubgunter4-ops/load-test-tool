import stat
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class ScriptTests(unittest.TestCase):
    def test_executable_scripts_exist(self):
        for name in ("loadtest", "loadtest-cli", "loadtest-gui"):
            script = ROOT / "scripts" / name
            self.assertTrue(script.is_file(), f"Falta el script ejecutable {script}")
            self.assertTrue(script.stat().st_mode & stat.S_IXUSR, f"El script {script} no es ejecutable")


if __name__ == "__main__":
    unittest.main()
