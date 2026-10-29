"""Myanmar fonts on Linux live in subfolders. The desktop scanner must still see them."""
import sys
import unittest
from pathlib import Path

APP = Path(__file__).resolve().parents[1] / "app"
if str(APP) not in sys.path:
    sys.path.insert(0, str(APP))

from subtitle_fonts import myanmar_fonts  # noqa: E402


class MyanmarFontTests(unittest.TestCase):
    def test_scan_finds_noto_when_the_package_is_installed(self):
        families = myanmar_fonts(refresh=True)
        self.assertIsInstance(families, dict)
        noto = Path("/usr/share/fonts/truetype/noto/NotoSansMyanmar-Regular.ttf")
        if not noto.is_file():
            self.skipTest("Noto Sans Myanmar is not installed on this machine")
        self.assertIn("Noto Sans Myanmar", families)
        self.assertTrue(any(Path(path) == noto for path in families["Noto Sans Myanmar"]))


if __name__ == "__main__":
    unittest.main()
