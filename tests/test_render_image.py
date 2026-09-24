import importlib
import tempfile
import unittest
from datetime import date
from pathlib import Path

from PIL import Image


class RenderImageTests(unittest.TestCase):
    def test_both_variants_leave_creator_footer_empty(self):
        for variant in ("old_stats", "new_stats"):
            with self.subTest(variant=variant), tempfile.TemporaryDirectory() as directory:
                module = importlib.import_module(f"{variant}.eia_stats")
                tables = [module.StatsTable(title, [("Total US:", 0.8, 210.0)])
                          for title in ("Gasoline", "Distillates", "Jet", "Crude")]
                path = module.render_image(tables, date(2026, 9, 18), Path(directory) / "stats.png")
                with Image.open(path) as image:
                    self.assertEqual(image.size, (1300, 280))
                    footer = image.crop((1000, 252, 1300, 280))
                    self.assertEqual(footer.getcolors(), [(300 * 28, (250, 249, 242))])


if __name__ == "__main__":
    unittest.main()
