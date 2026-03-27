import itertools
import unittest

from .search_scraper import Search
from .test_util import CachedHTMLProvider


class TestSearchScraper(unittest.TestCase):
    def setUp(self):
        self.sut = Search(CachedHTMLProvider().get_HTML)

    def test_fetch_search_videos(self):
        """Search for videos returns pagination tuple and results."""
        gen = self.sut.fetch_search("climate", 1, "t")
        a, b, c = next(itertools.islice(gen, 1))

        # Pagination: a=start, b=end, c=total
        self.assertIsInstance(a, int)
        self.assertIsInstance(b, int)
        self.assertIsInstance(c, int)
        self.assertGreater(c, 0)

        results = list(gen)
        self.assertGreater(len(results), 0)

        # Each result is (title, url, thumb, description)
        for title, url, thumb, desc in results:
            self.assertIsInstance(title, str)
            self.assertTrue(url.startswith("https://www.ted.com/talks/"))
            self.assertIsInstance(thumb, str)
            self.assertIsInstance(desc, str)

    def test_fetch_search_playlists(self):
        """Search for playlists returns playlist URLs."""
        gen = self.sut.fetch_search("science", 1, "p")
        a, b, c = next(itertools.islice(gen, 1))
        self.assertGreater(c, 0)

        results = list(gen)
        self.assertGreater(len(results), 0)
        for title, url, thumb, desc in results:
            self.assertTrue(url.startswith("https://www.ted.com/playlists/"))

    def test_fetch_search_pagination(self):
        """Search pagination tuple has valid values."""
        gen = self.sut.fetch_search("climate", 1, "t")
        a, b, c = next(itertools.islice(gen, 1))
        self.assertGreaterEqual(a, 1)
        self.assertGreaterEqual(b, a)
        self.assertGreaterEqual(c, b)
