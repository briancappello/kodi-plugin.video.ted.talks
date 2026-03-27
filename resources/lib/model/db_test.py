"""Tests for the TED catalog database layer."""

import os
import tempfile
import unittest

from .db import TedDatabase


class TestTedDatabase(unittest.TestCase):
    def setUp(self):
        self.db_fd, self.db_path = tempfile.mkstemp(suffix=".db")
        self.db = TedDatabase(self.db_path)

    def tearDown(self):
        self.db.close()
        os.close(self.db_fd)
        os.unlink(self.db_path)

    def _make_talk(self, object_id="1", slug="test_talk", title="Test Talk", **kwargs):
        talk = {
            "object_id": object_id,
            "slug": slug,
            "title": title,
            "duration": 600.0,
            "thumb_url": "https://example.com/thumb.jpg",
            "api_rank": 0,
            "description": None,
            "published_at": None,
            "recorded_at": None,
            "view_count": None,
        }
        talk.update(kwargs)
        return talk

    # -- Schema --

    def test_schema_creates_tables(self):
        tables = self.db.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
        table_names = {r["name"] for r in tables}
        self.assertIn("talks", table_names)
        self.assertIn("speakers", table_names)
        self.assertIn("topics", table_names)
        self.assertIn("talk_speakers", table_names)
        self.assertIn("talk_topics", table_names)
        self.assertIn("sync_meta", table_names)
        self.assertIn("talks_fts", table_names)

    def test_schema_version_stored(self):
        version = self.db.get_sync_meta("schema_version")
        self.assertEqual(version, "1")

    # -- Sync metadata --

    def test_sync_meta_roundtrip(self):
        self.db.set_sync_meta("test_key", "test_value")
        self.assertEqual(self.db.get_sync_meta("test_key"), "test_value")

    def test_needs_sync_true_when_never_synced(self):
        self.assertTrue(self.db.needs_sync("never_synced"))

    def test_needs_sync_false_after_mark(self):
        self.db.mark_synced("test_sync")
        self.assertFalse(self.db.needs_sync("test_sync", max_age_hours=1))

    # -- Talk CRUD --

    def test_upsert_and_get_talk(self):
        talk = self._make_talk(slug="my_talk", title="My Talk")
        self.db.upsert_talks([talk])
        result = self.db.get_talk_by_slug("my_talk")
        self.assertIsNotNone(result)
        self.assertEqual(result["title"], "My Talk")

    def test_upsert_updates_existing(self):
        talk = self._make_talk(description=None)
        self.db.upsert_talks([talk])
        talk["description"] = "Updated description"
        self.db.upsert_talks([talk])
        result = self.db.get_talk_by_slug("test_talk")
        self.assertEqual(result["description"], "Updated description")

    def test_upsert_coalesce_preserves_existing(self):
        """COALESCE should not overwrite existing non-null values with null."""
        talk = self._make_talk(description="Original")
        self.db.upsert_talks([talk])
        # Upsert again without description (None)
        talk2 = self._make_talk(description=None)
        self.db.upsert_talks([talk2])
        result = self.db.get_talk_by_slug("test_talk")
        self.assertEqual(result["description"], "Original")

    def test_get_talk_count(self):
        self.assertEqual(self.db.get_talk_count(), 0)
        self.db.upsert_talks([self._make_talk()])
        self.assertEqual(self.db.get_talk_count(), 1)

    # -- Newest query --

    def test_get_newest_ordered_by_rank(self):
        self.db.upsert_talks(
            [
                self._make_talk("1", "talk_a", "Talk A", api_rank=2),
                self._make_talk("2", "talk_b", "Talk B", api_rank=0),
                self._make_talk("3", "talk_c", "Talk C", api_rank=1),
            ]
        )
        results = self.db.get_newest(limit=10)
        titles = [r["title"] for r in results]
        self.assertEqual(titles, ["Talk B", "Talk C", "Talk A"])

    def test_get_newest_pagination(self):
        for i in range(10):
            self.db.upsert_talks(
                [self._make_talk(str(i), f"talk_{i}", f"Talk {i}", api_rank=i)]
            )
        page1 = self.db.get_newest(limit=3, offset=0)
        page2 = self.db.get_newest(limit=3, offset=3)
        self.assertEqual(len(page1), 3)
        self.assertEqual(len(page2), 3)
        self.assertNotEqual(page1[0]["slug"], page2[0]["slug"])

    # -- Enrichment --

    def test_enrich_talk_updates_fields(self):
        self.db.upsert_talks([self._make_talk()])
        self.db.enrich_talk(
            "test_talk",
            {
                "description": "A great talk",
                "published_at": "2025-03-15T12:00:00Z",
                "view_count": 50000,
                "topics": ["science", "technology"],
                "speakers": [{"name": "Jane Doe", "slug": "jane_doe"}],
            },
        )
        result = self.db.get_talk_by_slug("test_talk")
        self.assertEqual(result["description"], "A great talk")
        self.assertEqual(result["published_at"], "2025-03-15T12:00:00Z")
        self.assertEqual(result["view_count"], 50000)
        self.assertIsNotNone(result["enriched_at"])
        self.assertEqual(result["speaker_names"], "Jane Doe")

    def test_enrich_sets_topics(self):
        self.db.upsert_talks([self._make_talk()])
        self.db.enrich_talk(
            "test_talk",
            {
                "topics": ["AI", "robotics"],
            },
        )
        topics = self.db.get_topics()
        topic_names = [t["name"] for t in topics]
        self.assertIn("AI", topic_names)
        self.assertIn("robotics", topic_names)

    # -- Speakers --

    def test_set_talk_speakers_from_api(self):
        self.db.upsert_talks([self._make_talk()])
        self.db.set_talk_speakers_from_api("1", " Jane Doe, John Smith")
        self.db.conn.commit()
        result = self.db.get_talk_by_slug("test_talk")
        self.assertIn("Jane Doe", result["speaker_names"])
        self.assertIn("John Smith", result["speaker_names"])

    def test_get_speakers(self):
        self.db.upsert_talks([self._make_talk()])
        self.db.set_talk_speakers_from_api("1", "Jane Doe")
        self.db.conn.commit()
        speakers = self.db.get_speakers()
        self.assertEqual(len(speakers), 1)
        self.assertEqual(speakers[0]["name"], "Jane Doe")
        self.assertEqual(speakers[0]["talk_count"], 1)

    def test_get_talks_by_speaker(self):
        self.db.upsert_talks(
            [
                self._make_talk("1", "talk_1", "Talk 1"),
                self._make_talk("2", "talk_2", "Talk 2"),
            ]
        )
        self.db.set_talk_speakers_from_api("1", "Jane Doe")
        self.db.set_talk_speakers_from_api("2", "Jane Doe")
        self.db.conn.commit()
        talks = self.db.get_talks_by_speaker("Jane Doe")
        self.assertEqual(len(talks), 2)

    # -- Topics --

    def test_get_talks_by_topic(self):
        self.db.upsert_talks([self._make_talk()])
        self.db.enrich_talk("test_talk", {"topics": ["science"]})
        talks = self.db.get_talks_by_topic("science")
        self.assertEqual(len(talks), 1)
        self.assertEqual(talks[0]["title"], "Test Talk")

    # -- FTS5 search --

    def test_search_by_title(self):
        self.db.upsert_talks(
            [
                self._make_talk(
                    "1", "talk_ai", "The future of artificial intelligence"
                ),
                self._make_talk("2", "talk_cook", "How to cook pasta"),
            ]
        )
        results = self.db.search_talks("artificial intelligence")
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["slug"], "talk_ai")

    def test_search_by_description(self):
        self.db.upsert_talks(
            [self._make_talk(description="quantum computing advances")]
        )
        # FTS trigger fires on INSERT. But description was set via upsert_talk
        # which inserts with description. Let's verify.
        results = self.db.search_talks("quantum")
        self.assertEqual(len(results), 1)

    def test_search_prefix_matching(self):
        self.db.upsert_talks([self._make_talk(title="Neuroscience of sleep")])
        results = self.db.search_talks("neuro")
        self.assertEqual(len(results), 1)

    def test_search_no_results(self):
        self.db.upsert_talks([self._make_talk()])
        results = self.db.search_talks("xyznonexistent")
        self.assertEqual(len(results), 0)

    def test_search_empty_query(self):
        results = self.db.search_talks("")
        self.assertEqual(results, [])

    # -- Context manager --

    def test_context_manager(self):
        with TedDatabase(self.db_path) as db:
            db.upsert_talks([self._make_talk()])
            self.assertEqual(db.get_talk_count(), 1)


class TestTedDatabaseCreatesDirectory(unittest.TestCase):
    def test_creates_parent_directory(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "subdir", "nested", "test.db")
            with TedDatabase(db_path) as db:
                db.upsert_talks(
                    [
                        {
                            "object_id": "1",
                            "slug": "test",
                            "title": "Test",
                        }
                    ]
                )
                self.assertEqual(db.get_talk_count(), 1)
            self.assertTrue(os.path.exists(db_path))


if __name__ == "__main__":
    unittest.main()
