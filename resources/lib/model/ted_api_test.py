"""Tests for the TED API client."""

import json
import os
import unittest
from unittest.mock import patch, MagicMock

from . import ted_api

FIXTURES_DIR = os.path.join(os.path.dirname(__file__), "fixtures")


def _load_fixture(name):
    with open(os.path.join(FIXTURES_DIR, name)) as f:
        return json.load(f)


def _mock_post(fixture_name):
    """Create a mock requests.post that returns fixture data."""
    data = _load_fixture(fixture_name)
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = data
    mock_resp.raise_for_status.return_value = None
    return mock_resp


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


class TestTedApiSearch(unittest.TestCase):
    """Test API search using fixture data."""

    @patch("resources.lib.model.ted_api._post_json")
    def test_search_newest(self, mock_post_json):
        mock_post_json.return_value = _load_fixture("api_newest.json")
        result = ted_api.search(query="", page=0, hits_per_page=3)
        self.assertIn("hits", result)
        self.assertIn("total", result)
        self.assertIn("pages", result)
        self.assertEqual(len(result["hits"]), 3)
        self.assertGreater(result["total"], 1000)
        hit = result["hits"][0]
        self.assertIn("object_id", hit)
        self.assertIn("slug", hit)
        self.assertIn("title", hit)

    @patch("resources.lib.model.ted_api._post_json")
    def test_search_with_query(self, mock_post_json):
        mock_post_json.return_value = _load_fixture("api_search_climate.json")
        result = ted_api.search(query="climate", hits_per_page=5)
        self.assertGreater(len(result["hits"]), 0)
        self.assertGreater(result["total"], 10)

    @patch("resources.lib.model.ted_api._post_json")
    def test_search_pagination(self, mock_post_json):
        fixture_p0 = _load_fixture("api_page0.json")
        fixture_p1 = _load_fixture("api_page1.json")
        mock_post_json.side_effect = [fixture_p0, fixture_p1]

        page0 = ted_api.search(page=0, hits_per_page=2)
        page1 = ted_api.search(page=1, hits_per_page=2)
        self.assertNotEqual(
            page0["hits"][0]["object_id"],
            page1["hits"][0]["object_id"],
        )

    @patch("resources.lib.model.ted_api._post_json")
    def test_get_all_tags(self, mock_post_json):
        mock_post_json.return_value = _load_fixture("api_tags.json")
        tags = ted_api.get_all_tags(max_values=10)
        self.assertGreater(len(tags), 5)
        # Tags fixture should have common topics
        self.assertTrue(
            any(t in tags for t in ["science", "technology", "culture"]),
            "Expected common tags in fixture data",
        )


if __name__ == "__main__":
    unittest.main()
