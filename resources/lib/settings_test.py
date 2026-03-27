import unittest
from . import settings


class TestSettings(unittest.TestCase):
    def setUp(self):
        self.original_subtitle_languages = settings.__subtitle_languages__

    def tearDown(self):
        settings.__subtitle_languages__ = self.original_subtitle_languages

    def test_get_subtitle_languages_disabled(self):
        settings.__subtitle_languages__ = None
        self.assertIsNone(settings.get_subtitle_languages())

    def test_get_subtitle_languages_enabled(self):
        settings.__subtitle_languages__ = ["en"]
        self.assertEqual(["en"], settings.get_subtitle_languages())

    def test_get_subtitle_languages_multiple(self):
        settings.__subtitle_languages__ = ["en", "fr", "de"]
        self.assertEqual(["en", "fr", "de"], settings.get_subtitle_languages())

    def test_default_ted_url(self):
        self.assertEqual("https://www.ted.com", settings.__ted_url__)

    def test_default_plugin_id(self):
        self.assertEqual("plugin.video.ted.talks", settings.__plugin_id__)

    def test_temp_path_default(self):
        """Before initialize(), temp_path should be None."""
        # In test env, initialize() is not called
        self.assertIsNone(settings.__temp_path__)
