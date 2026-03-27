"""Tests for the TED API client."""

import unittest

from . import ted_api


class TestTedApiNormalization(unittest.TestCase):
    """Test data normalization without hitting the network."""

    def test_normalize_hit(self):
        hit = {
            "objectID": "12345",
            "slug": "jane_doe_test_talk",
            "title": " Test Talk ",
            "duration": "600.5",
            "speakers": " Jane Doe",
            "photos": [
                {
                    "photo_sizes": [
                        {
                            "talkstar_aspect_ratio_id": 2,
                            "width": 1920,
                            "height": 1080,
                            "url": "https://example.com/16x9.jpg",
                        },
                        {
                            "talkstar_aspect_ratio_id": 3,
                            "width": 2400,
                            "height": 1800,
                            "url": "https://example.com/4x3.jpg",
                        },
                        {
                            "talkstar_aspect_ratio_id": 36,
                            "width": 1350,
                            "height": 675,
                            "url": "https://example.com/2x1.jpg",
                        },
                    ]
                }
            ],
        }
        result = ted_api._normalize_hit(hit, rank_offset=10, index=3)
        self.assertEqual(result["object_id"], "12345")
        self.assertEqual(result["slug"], "jane_doe_test_talk")
        self.assertEqual(result["title"], "Test Talk")
        self.assertEqual(result["duration"], 600.5)
        self.assertEqual(result["speakers_str"], "Jane Doe")
        self.assertEqual(result["api_rank"], 13)
        # Should pick 2:1 (ratio 36) as preferred
        self.assertEqual(result["thumb_url"], "https://example.com/2x1.jpg")

    def test_normalize_hit_no_photos(self):
        hit = {"objectID": "1", "slug": "x", "title": "X", "photos": []}
        result = ted_api._normalize_hit(hit)
        self.assertIsNone(result["thumb_url"])

    def test_normalize_hit_missing_fields(self):
        hit = {"objectID": "1", "slug": "x", "title": "X"}
        result = ted_api._normalize_hit(hit)
        self.assertIsNone(result["duration"])
        self.assertEqual(result["speakers_str"], "")

    def test_parse_duration(self):
        self.assertEqual(ted_api._parse_duration("600.5"), 600.5)
        self.assertEqual(ted_api._parse_duration("0"), 0.0)
        self.assertIsNone(ted_api._parse_duration(None))
        self.assertIsNone(ted_api._parse_duration("invalid"))

    def test_pick_thumbnail_prefers_2x1(self):
        photos = [
            {
                "photo_sizes": [
                    {"talkstar_aspect_ratio_id": 2, "width": 1920, "url": "16x9.jpg"},
                    {"talkstar_aspect_ratio_id": 36, "width": 1350, "url": "2x1.jpg"},
                ]
            }
        ]
        self.assertEqual(ted_api._pick_thumbnail(photos), "2x1.jpg")

    def test_pick_thumbnail_fallback_widest(self):
        photos = [
            {
                "photo_sizes": [
                    {"talkstar_aspect_ratio_id": 99, "width": 800, "url": "small.jpg"},
                    {"talkstar_aspect_ratio_id": 100, "width": 1600, "url": "big.jpg"},
                ]
            }
        ]
        self.assertEqual(ted_api._pick_thumbnail(photos), "big.jpg")


class TestTedApiLive(unittest.TestCase):
    """Tests that hit the live TED API. May be slow."""

    def test_search_newest(self):
        result = ted_api.search(query="", page=0, hits_per_page=3)
        self.assertIn("hits", result)
        self.assertIn("total", result)
        self.assertIn("pages", result)
        self.assertEqual(len(result["hits"]), 3)
        self.assertGreater(result["total"], 1000)
        # Each hit should have required fields
        hit = result["hits"][0]
        self.assertIn("object_id", hit)
        self.assertIn("slug", hit)
        self.assertIn("title", hit)

    def test_search_with_query(self):
        result = ted_api.search(query="climate", hits_per_page=5)
        self.assertGreater(len(result["hits"]), 0)
        # At least one result should mention climate
        titles = " ".join(h["title"].lower() for h in result["hits"])
        # Not guaranteed but highly likely
        self.assertGreater(result["total"], 10)

    def test_search_pagination(self):
        page0 = ted_api.search(page=0, hits_per_page=2)
        page1 = ted_api.search(page=1, hits_per_page=2)
        self.assertNotEqual(
            page0["hits"][0]["object_id"],
            page1["hits"][0]["object_id"],
        )

    def test_get_all_tags(self):
        tags = ted_api.get_all_tags(max_values=10)
        self.assertGreater(len(tags), 5)
        # Science and technology should be top tags
        self.assertIn("science", tags)
        self.assertIn("technology", tags)


if __name__ == "__main__":
    unittest.main()
