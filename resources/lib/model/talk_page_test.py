"""Tests for the talk page extractor."""

import json
import os
import unittest

from . import talk_page


def _make_page_html(video_data, player_data=None):
    """Build a minimal HTML page with __NEXT_DATA__ JSON."""
    if player_data is None:
        player_data = {
            "id": "12345",
            "resources": {
                "hls": {"stream": "https://example.com/stream.m3u8?intro=1"},
                "h264": [{"bitrate": 1200, "file": "https://example.com/video.mp4"}],
            },
            "event": "TED2025",
            "languages": [
                {"languageCode": "en"},
                {"languageCode": "es"},
            ],
        }
    if isinstance(player_data, dict):
        player_data = json.dumps(player_data)

    data = {
        "props": {
            "pageProps": {
                "videoData": {
                    "playerData": player_data,
                    **video_data,
                }
            }
        }
    }
    return (
        '<html><script id="__NEXT_DATA__" type="application/json">%s</script></html>'
        % json.dumps(data)
    )


class TestExtractEnrichment(unittest.TestCase):
    def test_basic_enrichment(self):
        html = _make_page_html(
            {
                "title": "Test Talk",
                "description": "A great talk about testing",
                "publishedAt": "2025-03-15T12:00:00Z",
                "recordedOn": "2025-03-10",
                "viewedCount": 50000,
                "presenterDisplayName": "Jane Doe",
                "topics": {
                    "nodes": [
                        {"name": "science"},
                        {"name": "technology"},
                    ]
                },
                "speakers": {
                    "nodes": [
                        {
                            "firstname": "Jane",
                            "lastname": "Doe",
                            "slug": "jane_doe",
                            "photoUrl": "https://example.com/jane.jpg",
                            "whoTheyAre": "A scientist",
                        },
                    ]
                },
            }
        )
        result = talk_page.extract_enrichment(html)
        self.assertEqual(result["description"], "A great talk about testing")
        self.assertEqual(result["published_at"], "2025-03-15T12:00:00Z")
        self.assertEqual(result["recorded_at"], "2025-03-10")
        self.assertEqual(result["view_count"], 50000)
        self.assertEqual(
            result["topics"],
            [{"name": "Science", "slug": ""}, {"name": "Technology", "slug": ""}],
        )
        self.assertEqual(len(result["speakers"]), 1)
        self.assertEqual(result["speakers"][0]["name"], "Jane Doe")
        self.assertEqual(result["speakers"][0]["slug"], "jane_doe")

    def test_enrichment_no_speaker_nodes(self):
        html = _make_page_html(
            {
                "title": "Test",
                "description": "",
                "presenterDisplayName": "Bob Smith",
                "speakers": {"nodes": []},
                "topics": {"nodes": []},
            }
        )
        result = talk_page.extract_enrichment(html)
        self.assertEqual(len(result["speakers"]), 1)
        self.assertEqual(result["speakers"][0]["name"], "Bob Smith")

    def test_enrichment_empty_html(self):
        result = talk_page.extract_enrichment("<html></html>")
        self.assertEqual(result, {})


class TestExtractStreamInfo(unittest.TestCase):
    def test_hls_stream(self):
        html = _make_page_html(
            {
                "title": "Test Talk",
                "description": "Desc",
                "duration": 600,
                "recordedOn": "2025-03-10",
                "publishedAt": "2025-03-15T12:00:00Z",
                "presenterDisplayName": "Jane Doe",
                "slug": "test_talk",
                "primaryImageSet": [{"url": "https://example.com/thumb.jpg"}],
            }
        )
        result = talk_page.extract_stream_info(html)
        # Query string should be stripped
        self.assertEqual(result["stream"], "https://example.com/stream.m3u8")
        self.assertEqual(result["title"], "Test Talk")
        self.assertEqual(result["duration"], 600)
        self.assertEqual(result["date"], "10.03.2025")
        self.assertEqual(result["aired"], "2025-03-10")
        self.assertEqual(result["speaker_name"], "Jane Doe")

    def test_h264_fallback(self):
        html = _make_page_html(
            {
                "title": "T",
                "description": "",
                "duration": 0,
                "recordedOn": "",
                "publishedAt": "",
                "slug": "",
                "presenterDisplayName": "",
                "primaryImageSet": [],
            },
            player_data={
                "id": "1",
                "resources": {
                    "hls": {},
                    "h264": [{"file": "https://example.com/v.mp4"}],
                },
                "languages": [],
            },
        )
        result = talk_page.extract_stream_info(html)
        self.assertEqual(result["stream"], "https://example.com/v.mp4")

    def test_youtube_fallback(self):
        html = _make_page_html(
            {
                "title": "T",
                "description": "",
                "duration": 0,
                "recordedOn": "",
                "publishedAt": "",
                "slug": "",
                "presenterDisplayName": "",
                "primaryImageSet": [],
            },
            player_data={
                "id": "1",
                "resources": {"hls": {}, "h264": []},
                "external": {"service": "YouTube", "code": "dQw4w9WgXcQ"},
                "languages": [],
            },
        )
        result = talk_page.extract_stream_info(html)
        self.assertIn("youtube.com", result["stream"])
        self.assertIn("dQw4w9WgXcQ", result["stream"])

    def test_subtitles_with_language_preference(self):
        html = _make_page_html(
            {
                "title": "T",
                "description": "",
                "duration": 0,
                "recordedOn": "",
                "publishedAt": "",
                "slug": "",
                "presenterDisplayName": "",
                "primaryImageSet": [],
            }
        )
        result = talk_page.extract_stream_info(html, subtitle_languages=["es", "en"])
        # Should pick Spanish since it's first and available
        self.assertIsNotNone(result["subtitles"])
        if isinstance(result["subtitles"], str):
            self.assertIn("lang/es", result["subtitles"])

    def test_no_subtitles_when_disabled(self):
        html = _make_page_html(
            {
                "title": "T",
                "description": "",
                "duration": 0,
                "recordedOn": "",
                "publishedAt": "",
                "slug": "",
                "presenterDisplayName": "",
                "primaryImageSet": [],
            }
        )
        result = talk_page.extract_stream_info(html, subtitle_languages=None)
        self.assertIsNone(result["subtitles"])

    def test_empty_html(self):
        result = talk_page.extract_stream_info("<html></html>")
        self.assertIsNone(result["stream"])


class TestResolveSubtitles(unittest.TestCase):
    def test_resolve_none(self):
        self.assertIsNone(talk_page.resolve_subtitles(None, None))

    def test_resolve_ted_api_url(self):
        """TED API subtitle URL should be converted to cached SRT."""
        import tempfile, os

        subtitle_json = json.dumps(
            {
                "captions": [
                    {"startTime": 0, "duration": 1000, "content": "Hello"},
                    {"startTime": 1000, "duration": 2000, "content": "World"},
                ]
            }
        )
        fetch_fn = lambda url: subtitle_json
        with tempfile.TemporaryDirectory() as tmpdir:
            result = talk_page.resolve_subtitles(
                fetch_fn,
                "https://www.ted.com/talks/subtitles/id/123/lang/en",
                cache_dir=tmpdir,
            )
            self.assertIsNotNone(result)
            self.assertTrue(result.endswith(".srt"))
            with open(result) as f:
                content = f.read()
            self.assertIn("Hello", content)
            self.assertIn("-->", content)

    def test_resolve_metadata_with_webvtt(self):
        """Metadata-based subtitles should return webvtt URL."""
        metadata_json = json.dumps(
            {
                "subtitles": [
                    {"code": "en", "webvtt": "https://example.com/subs.vtt"},
                ]
            }
        )
        fetch_fn = lambda url: metadata_json
        subtitle_info = {
            "type": "metadata",
            "metadata_url": "https://example.com/metadata.json",
            "language": "en",
            "fallback": "https://www.ted.com/talks/subtitles/id/123/lang/en",
        }
        result = talk_page.resolve_subtitles(fetch_fn, subtitle_info)
        self.assertEqual(result, "https://example.com/subs.vtt")


class TestTalkPageFixture(unittest.TestCase):
    """Tests using a captured talk page HTML fixture."""

    @classmethod
    def setUpClass(cls):
        fixtures_dir = os.path.join(os.path.dirname(__file__), "fixtures")
        with open(os.path.join(fixtures_dir, "talk_page.html")) as f:
            cls.html = f.read()

    def test_extract_enrichment_fixture(self):
        result = talk_page.extract_enrichment(self.html)
        self.assertIn("description", result)
        self.assertGreater(len(result["description"]), 10)
        self.assertGreater(len(result["topics"]), 0)
        self.assertGreater(len(result["speakers"]), 0)

    def test_extract_stream_info_fixture(self):
        result = talk_page.extract_stream_info(self.html, subtitle_languages=["en"])
        self.assertIsNotNone(result["stream"])
        self.assertGreater(len(result["title"]), 0)
        self.assertGreater(result["duration"], 0)


if __name__ == "__main__":
    unittest.main()
