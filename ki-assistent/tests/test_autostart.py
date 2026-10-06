import os
import unittest
from unittest import mock

from angel import autostart
from tests.helpers import TempDirTest


class AutostartTest(TempDirTest):
    def test_enable_disable_status(self):
        env = {"HOME": str(self.tmp), "XDG_CONFIG_HOME": str(self.tmp / ".config"),
               "APPDATA": str(self.tmp / "AppData")}
        with mock.patch.dict(os.environ, env, clear=False):
            if os.name == "nt":
                self.skipTest("Windows-Verknüpfung braucht PowerShell")
            self.assertFalse(autostart.is_enabled())
            msg = autostart.enable()
            self.assertIn("eingerichtet", msg)
            self.assertTrue(autostart.is_enabled())
            f = autostart._linux_file()
            self.assertIn("-m angel", f.read_text(encoding="utf-8"))
            autostart.disable()
            self.assertFalse(autostart.is_enabled())


if __name__ == "__main__":
    unittest.main()
