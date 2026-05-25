import os
import unittest
from PIL import Image

from Utilities.logo_transparency_cleaner import clean_logo_background


class TestLogoCleanser(unittest.TestCase):
    def setUp(self):
        # ensure input logo exists (the repo contains logo/Brand_logo.png)
        self.input = "logo/Brand_logo.png"
        self.output = "assets/logo/brand_logo_clean.png"
        os.makedirs(os.path.dirname(self.output), exist_ok=True)
        try:
            os.remove(self.output)
        except Exception:
            pass

    def test_cleaner_produces_transparent_png(self):
        self.assertTrue(os.path.exists(self.input), "source logo is missing")
        result = clean_logo_background(self.input, self.output)
        self.assertTrue(os.path.exists(self.output), "cleaned logo not written")
        img = Image.open(self.output)
        self.assertEqual(img.mode, "RGBA")

        # verify at least one pixel became transparent
        pix = img.load()
        found = False
        for y in range(img.height):
            for x in range(img.width):
                if pix[x, y][3] == 0:
                    found = True
                    break
            if found:
                break
        self.assertTrue(found, "no transparent pixels detected")
        # result should reflect whether white pixels were found
        self.assertEqual(result, found)
