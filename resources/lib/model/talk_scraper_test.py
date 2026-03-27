import unittest

from .talk_scraper import Talk
from .test_util import CachedHTMLProvider


class TestTalkScraper(unittest.TestCase):
    def setUp(self):
        self.sut = Talk(CachedHTMLProvider().get_HTML)

    def test_fetch_talk_standard(self):
        """Test a standard TED talk with HLS stream."""
        stream, subtitles, vidinfo, artwork, speaker = self.sut.fetch_talk(
            "https://www.ted.com/talks/dan_bricklin_meet_the_inventor_of_the_electronic_spreadsheet"
        )
        self.assertIsNotNone(stream)
        self.assertIn("hls.ted.com", stream)
        self.assertIn("manifest.m3u8", stream)
        # Stream URL should have query string stripped
        self.assertNotIn("?", stream)

        self.assertEqual(
            "Meet the inventor of the electronic spreadsheet", vidinfo["title"]
        )
        self.assertIsNotNone(vidinfo["duration"])
        self.assertIsNotNone(vidinfo["plot"])
        self.assertIsNotNone(vidinfo["date"])
        self.assertEqual("video", vidinfo["mediatype"])

        self.assertIsNotNone(artwork["thumb"])
        self.assertIsNotNone(artwork["icon"])

        self.assertEqual("Dan Bricklin", speaker["name"])
        self.assertIsNotNone(speaker["url"])

    def test_fetch_talk_youtube_fallback(self):
        """Test a talk that has a YouTube external link (no HLS stream)."""
        stream, subtitles, vidinfo, artwork, speaker = self.sut.fetch_talk(
            "https://www.ted.com/talks/seth_godin_this_is_broken"
        )
        # Without YDStreamExtractor this will be None in test env
        # The important thing is it doesn't crash
        self.assertEqual("This is broken", vidinfo["title"])
        self.assertEqual("Seth Godin", speaker["name"])
        self.assertIsNotNone(vidinfo["plot"])

    def test_fetch_talk_vidinfo_fields(self):
        """Verify all expected vidinfo fields are present."""
        stream, subtitles, vidinfo, artwork, speaker = self.sut.fetch_talk(
            "https://www.ted.com/talks/dan_bricklin_meet_the_inventor_of_the_electronic_spreadsheet"
        )
        expected_keys = {
            "title",
            "duration",
            "date",
            "aired",
            "dateadded",
            "plot",
            "genre",
            "mediatype",
            "url",
        }
        self.assertTrue(expected_keys.issubset(set(vidinfo.keys())))

    def test_fetch_talk_with_series_info(self):
        """Verify season/episode info is added when provided."""
        stream, subtitles, vidinfo, artwork, speaker = self.sut.fetch_talk(
            "https://www.ted.com/talks/dan_bricklin_meet_the_inventor_of_the_electronic_spreadsheet",
            season="1",
            episode="3",
            tvshow="Test Show",
        )
        self.assertEqual(1, vidinfo["season"])
        self.assertEqual(3, vidinfo["episode"])
        self.assertEqual("episode", vidinfo["mediatype"])
        self.assertEqual("Test Show", vidinfo["tvshowtitle"])
