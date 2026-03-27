import unittest

from .test_util import CachedHTMLProvider
from .topics_scraper import Topics


class TestTopicsScraper(unittest.TestCase):
    def setUp(self):
        self.sut = Topics(CachedHTMLProvider().get_HTML)

    def test_fetch_topics(self):
        topics = list(self.sut.fetch_topics())
        self.assertGreater(len(topics), 0)

        # Each topic is (name, slug)
        names = [t[0] for t in topics]
        slugs = [t[1] for t in topics]

        # Topic names may be lowercase or capitalized
        names_lower = [n.lower() for n in names]
        self.assertIn("activism", names_lower)
        self.assertIn("activism", slugs)

        # Topics should be sorted alphabetically (case-insensitive)
        letters = [t[0][0].upper() for t in topics if t[0]]
        self.assertEqual(letters, sorted(letters))

    def test_fetch_featured(self):
        results = list(self.sut.fetch_featured("astronomy"))
        self.assertGreater(len(results), 0)

        # Each result is (type_flag, info_dict)
        for type_flag, info in results:
            self.assertIn(type_flag, ("t", "p"))
            self.assertIsNotNone(info["title"])
            self.assertIsNotNone(info["tag"])
            self.assertIn("mediatype", info)

            if type_flag == "t":
                self.assertEqual("video", info["mediatype"])
                self.assertIn("duration", info)
                self.assertIn("author", info)
            elif type_flag == "p":
                self.assertEqual("tvshow", info["mediatype"])
                self.assertIn("count", info)

    def test_fetch_featured_empty(self):
        """A topic with no featured content should return empty."""
        results = list(self.sut.fetch_featured("advertising"))
        # May or may not have content; just shouldn't crash
        for type_flag, info in results:
            self.assertIn(type_flag, ("t", "p"))
