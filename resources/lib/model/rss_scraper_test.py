from datetime import datetime

try:
    from elementtree.ElementTree import fromstring
except ImportError:
    from xml.etree.ElementTree import fromstring

import unittest

from .rss_scraper import NewTalksRss


minimal_item = """
<item xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd"
      xmlns:media="http://search.yahoo.com/mrss/">
  <itunes:author>Dovahkiin</itunes:author>
  <itunes:subtitle>fus ro dah</itunes:subtitle>
  <itunes:summary>Unrelenting Force</itunes:summary>
  <itunes:duration>01:02:03</itunes:duration>
  <guid isPermaLink="false">eng.video.talk.ted.com:830</guid>
  <pubDate>Sat, 04 Feb 2012 08:14:00 +0000</pubDate>
  <media:thumbnail url="invalid://nowhere/nothing.jpg" width="42" height="42" />
  <media:content url="invalid://nowhere/nothing.mp4" />
  <link>invalid://nowhere/nothing.html</link>
</item>"""


class TestNewTalksRss(unittest.TestCase):
    def setUp(self):
        self.logger = lambda msg, **kw: None
        self.talks = NewTalksRss(self.logger)

    def test_get_talk_details_minimal(self):
        details = self.talks.get_talk_details(fromstring(minimal_item))
        self.assertEqual("fus ro dah", details["title"])
        self.assertEqual(["Dovahkiin"], details["cast"])
        self.assertEqual("invalid://nowhere/nothing.jpg", details["thumb"])
        self.assertEqual("invalid://nowhere/nothing.html", details["url"])
        self.assertEqual("invalid://nowhere/nothing.mp4", details["media"])
        self.assertEqual("Unrelenting Force", details["plot"])
        self.assertEqual("04.02.2012", details["date"])
        self.assertEqual("video", details["mediatype"])
        # Duration: 1*3600 + 2*60 + 3 = 3723
        self.assertEqual(3723, details["duration"])
        self.assertIn("dateadded", details)

    def test_get_talk_details_broken_date(self):
        """Gracefully handle unparseable dates."""
        document = fromstring(minimal_item)
        document.find("./pubDate").text = "Sat, 04 02 2012 08:14:00"
        details = self.talks.get_talk_details(document)
        date_now = datetime.strftime(datetime.now(), "%d.%m.%Y")
        self.assertEqual(date_now, details["date"])

    def test_get_talk_details_keys(self):
        """Verify all expected keys are present."""
        details = self.talks.get_talk_details(fromstring(minimal_item))
        expected_keys = {
            "title",
            "cast",
            "thumb",
            "plot",
            "duration",
            "date",
            "dateadded",
            "url",
            "media",
            "mediatype",
        }
        self.assertEqual(expected_keys, set(details.keys()))

    def test_smoke_live_feed(self):
        """Smoke test against the live RSS feed."""
        talks = list(self.talks.get_new_talks())
        self.assertGreater(len(talks), 10)
        talk = talks[0]
        self.assertIsNotNone(talk["title"])
        self.assertIsNotNone(talk["cast"])
        self.assertIsInstance(talk["cast"], list)
        self.assertIsNotNone(talk["date"])
        self.assertIsNotNone(talk["url"])
        self.assertIsNotNone(talk["thumb"])
        self.assertIsNotNone(talk["mediatype"])
        self.assertIsNotNone(talk["plot"])
        self.assertIsInstance(talk["duration"], int)
