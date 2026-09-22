import importlib
import struct
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from PIL import Image


class ClipboardTests(unittest.TestCase):
    def test_locked_preview_gets_new_output_without_overwriting(self):
        for variant in ("old_stats", "new_stats"):
            module = importlib.import_module(f"{variant}.eia_stats")
            with tempfile.TemporaryDirectory() as directory:
                target = Path(directory) / "eia_stats.png"
                target.write_bytes(b"previous image")
                original_replace = Path.replace

                def replace(source, destination):
                    if destination == target:
                        raise PermissionError("Viewer holds the previous file")
                    return original_replace(source, destination)

                with patch.object(Path, "replace", replace):
                    tables = [module.StatsTable(title, [("TOT", 1.0, 2.0)]) for title in ("CRUDE", "GASOLINE", "DISTILLATE", "JET")]
                    actual = module.render_image(tables, date(2026, 9, 11), target)
                self.assertNotEqual(actual, target)
                self.assertEqual(target.read_bytes(), b"previous image")
                with Image.open(actual) as image:
                    self.assertEqual(image.size, (1300, 280))

    def test_persistent_bitmap_bytes_and_cleanup(self):
        for variant in ("old_stats", "new_stats"):
            module = importlib.import_module(f"{variant}.eia_stats")
            clipboard = SimpleNamespace(CF_DIB=8, OpenClipboard=Mock(), EmptyClipboard=Mock(),
                                        SetClipboardData=Mock(), CloseClipboard=Mock())
            clipboard.GetClipboardData = lambda fmt: clipboard.SetClipboardData.call_args.args[1]
            with tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "image.png"
                Image.new("RGB", (3, 2), "red").save(path)
                with patch.object(module.platform, "system", return_value="Windows"), \
                     patch.dict(sys.modules, {"win32clipboard": clipboard, "pywintypes": SimpleNamespace(error=RuntimeError)}):
                    self.assertTrue(module.copy_image_to_clipboard(path))
                fmt, data = clipboard.SetClipboardData.call_args.args
                self.assertEqual(fmt, 8)
                self.assertEqual(struct.unpack_from("<Iii", data), (40, 3, 2))
                clipboard.CloseClipboard.assert_called_once()

    def test_access_denied_is_not_reported_as_success(self):
        for variant in ("old_stats", "new_stats"):
            module = importlib.import_module(f"{variant}.eia_stats")
            clipboard = SimpleNamespace(OpenClipboard=Mock(side_effect=RuntimeError("Access is denied")), CloseClipboard=Mock())
            with tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "image.png"
                Image.new("RGB", (3, 2), "red").save(path)
                with patch.object(module.platform, "system", return_value="Windows"), \
                     patch.dict(sys.modules, {"win32clipboard": clipboard, "pywintypes": SimpleNamespace(error=RuntimeError)}), \
                     patch.object(module, "print"):
                    self.assertFalse(module.copy_image_to_clipboard(path))
                clipboard.CloseClipboard.assert_not_called()
