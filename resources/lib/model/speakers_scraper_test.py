import itertools
import unittest

from .speakers_scraper import Speakers
from .test_util import CachedHTMLProvider


class TestSpeakersScraper(unittest.TestCase):
    def setUp(self):
        self.sut = Speakers(CachedHTMLProvider().get_HTML)

    def test_get_speaker_page_count(self):
        count = self.sut.get_speaker_page_count()
        self.assertGreaterEqual(count, 50)

    def test_get_speakers_for_pages(self):
        gen = self.sut.get_speakers_for_pages([1])
        page_count = next(itertools.islice(gen, 1))
        self.assertGreater(page_count, 1)

        speakers = list(gen)
        self.assertGreater(len(speakers), 0)

        # Each result is (name, url, img, tagline)
        for name, url, img, tagline in speakers:
            self.assertIsInstance(name, str)
            self.assertTrue(len(name) > 0)
            self.assertTrue(url.startswith("https://www.ted.com/speakers/"))
            self.assertIsInstance(tagline, str)

    def test_get_talks_for_speaker(self):
        gen = self.sut.get_talks_for_speaker(
            "https://www.ted.com/speakers/janine_benyus"
        )

        # First yield is speaker info tuple
        speaker_name, thumb, tagline, intro, extra = next(itertools.islice(gen, 1))
        self.assertIn("Janine", speaker_name)
        self.assertIsInstance(tagline, str)

        # Remaining yields are talk tuples from Talk.fetch_talk()
        talks = list(gen)
        self.assertGreaterEqual(len(talks), 2)

        # Each talk is (stream, subtitles, vidinfo, artwork, speaker)
        for stream, subtitles, vidinfo, artwork, speaker in talks:
            self.assertIsNotNone(vidinfo["title"])
            self.assertIsNotNone(vidinfo["url"])
