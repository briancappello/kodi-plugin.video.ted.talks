import itertools
import unittest

from .series_scraper import Series
from .test_util import CachedHTMLProvider


class TestSeriesScraper(unittest.TestCase):
    def setUp(self):
        self.sut = Series(CachedHTMLProvider().get_HTML)

    def test_fetch_list(self):
        """Verify the series list page returns series entries."""
        series_list = list(self.sut.fetch_list())
        self.assertGreater(len(series_list), 5)

        # Each entry is (title, url, img, plot)
        for title, url, img, plot in series_list:
            self.assertIsInstance(title, str)
            self.assertTrue(len(title) > 0, f"Empty title in series list")
            self.assertTrue(
                url.startswith("https://www.ted.com/series/"), f"Bad URL: {url}"
            )
            # img may be None for some entries
            self.assertIsInstance(plot, str)

    def test_fetch_list_known_series(self):
        """Verify a known series appears in the list."""
        series_list = list(self.sut.fetch_list())
        titles = [s[0] for s in series_list]
        self.assertIn("Small Thing Big Idea", titles)

    def test_fetch_series(self):
        """Verify fetching a specific series returns seasons."""
        gen = self.sut.fetch_series("https://www.ted.com/series/small_thing_big_idea")
        tvshow, md5 = next(itertools.islice(gen, 1))

        self.assertEqual("Small Thing Big Idea", tvshow["title"])
        self.assertIsNotNone(tvshow["plot"])
        self.assertEqual("tvshow", tvshow["mediatype"])
        self.assertIsInstance(md5, str)

        seasons = list(gen)
        self.assertGreater(len(seasons), 0)

        for season in seasons:
            self.assertIn("title", season)
            self.assertIn("season", season)
            self.assertEqual("season", season["mediatype"])
