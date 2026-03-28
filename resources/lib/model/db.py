"""
SQLite database layer for the TED Talks catalog.

Provides schema management, CRUD operations, FTS5 full-text search,
and sync state tracking. Designed to work both inside Kodi (using
xbmcvfs paths) and standalone (for CLI sync tools).
"""

import sqlite3
import os
import time

SCHEMA_VERSION = 1

# The schema is applied as a single transaction on first use.
_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS talks (
    object_id     TEXT PRIMARY KEY,
    slug          TEXT UNIQUE NOT NULL,
    title         TEXT NOT NULL,
    duration      REAL,
    thumb_url     TEXT,
    api_rank      INTEGER,
    description   TEXT,
    published_at  TEXT,
    recorded_at   TEXT,
    view_count    INTEGER,
    favorite      INTEGER DEFAULT 0,
    watched       INTEGER DEFAULT 0,
    resume_at     REAL,
    total_time    REAL,
    created_at    TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    updated_at    TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    enriched_at   TEXT
);

CREATE TABLE IF NOT EXISTS speakers (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    name      TEXT UNIQUE NOT NULL,
    slug      TEXT,
    thumb_url TEXT,
    bio       TEXT
);

CREATE TABLE IF NOT EXISTS topics (
    id   INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT UNIQUE NOT NULL,
    slug TEXT
);

CREATE TABLE IF NOT EXISTS talk_speakers (
    talk_id    TEXT    NOT NULL REFERENCES talks(object_id) ON DELETE CASCADE,
    speaker_id INTEGER NOT NULL REFERENCES speakers(id) ON DELETE CASCADE,
    PRIMARY KEY (talk_id, speaker_id)
);

CREATE TABLE IF NOT EXISTS talk_topics (
    talk_id  TEXT    NOT NULL REFERENCES talks(object_id) ON DELETE CASCADE,
    topic_id INTEGER NOT NULL REFERENCES topics(id) ON DELETE CASCADE,
    PRIMARY KEY (talk_id, topic_id)
);

CREATE VIRTUAL TABLE IF NOT EXISTS talks_fts USING fts5(
    title,
    description,
    speakers_text,
    content=talks,
    content_rowid=rowid,
    tokenize='porter unicode61'
);

CREATE TABLE IF NOT EXISTS sync_meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);

CREATE INDEX IF NOT EXISTS idx_talks_published ON talks(published_at DESC);
CREATE INDEX IF NOT EXISTS idx_talks_api_rank ON talks(api_rank);
CREATE INDEX IF NOT EXISTS idx_speakers_name ON speakers(name);
CREATE INDEX IF NOT EXISTS idx_topics_name ON topics(name);
"""

# Triggers to keep FTS5 in sync with the talks table.
_FTS_TRIGGERS_SQL = """
CREATE TRIGGER IF NOT EXISTS talks_ai AFTER INSERT ON talks BEGIN
    INSERT INTO talks_fts(rowid, title, description, speakers_text)
    VALUES (new.rowid, new.title, new.description, '');
END;

CREATE TRIGGER IF NOT EXISTS talks_ad AFTER DELETE ON talks BEGIN
    INSERT INTO talks_fts(talks_fts, rowid, title, description, speakers_text)
    VALUES ('delete', old.rowid, old.title, old.description, '');
END;

CREATE TRIGGER IF NOT EXISTS talks_au AFTER UPDATE OF title, description ON talks BEGIN
    INSERT INTO talks_fts(talks_fts, rowid, title, description, speakers_text)
    VALUES ('delete', old.rowid, old.title, old.description, '');
    INSERT INTO talks_fts(rowid, title, description, speakers_text)
    VALUES (new.rowid, new.title, new.description, '');
END;
"""


class TedDatabase:
    """
    Manages a SQLite database containing the TED Talks catalog.

    Usage:
        db = TedDatabase('/path/to/ted_catalog.db')
        talks = db.get_newest(limit=24, offset=0)
        db.close()

    Or as a context manager:
        with TedDatabase('/path/to/ted_catalog.db') as db:
            talks = db.get_newest(limit=24)
    """

    def __init__(self, db_path):
        self.db_path = db_path
        self._conn = None

    @property
    def conn(self):
        if self._conn is None:
            db_dir = os.path.dirname(self.db_path)
            if db_dir and not os.path.exists(db_dir):
                os.makedirs(db_dir, exist_ok=True)
            self._conn = sqlite3.connect(self.db_path, timeout=10)
            self._conn.row_factory = sqlite3.Row
            try:
                self._conn.execute("PRAGMA journal_mode=WAL")
            except sqlite3.OperationalError:
                pass  # Read-only DB
            self._conn.execute("PRAGMA foreign_keys=ON")
            self._init_schema()
        return self._conn

    def _init_schema(self):
        """Create tables if they don't exist. Skip if DB is read-only."""
        # Check if schema already exists
        row = self._conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='talks'"
        ).fetchone()
        if row:
            self._migrate_schema()
            return
        try:
            self._conn.executescript(_SCHEMA_SQL)
            self._conn.executescript(_FTS_TRIGGERS_SQL)
            self._conn.execute(
                "INSERT OR IGNORE INTO sync_meta(key, value) VALUES (?, ?)",
                ("schema_version", str(SCHEMA_VERSION)),
            )
            self._conn.commit()
        except sqlite3.OperationalError:
            pass  # Read-only DB, schema must already exist

    def _migrate_schema(self):
        """Apply schema migrations for existing databases."""
        try:
            cols = [
                r[1] for r in self._conn.execute("PRAGMA table_info(talks)").fetchall()
            ]
            if "favorite" not in cols:
                self._conn.execute(
                    "ALTER TABLE talks ADD COLUMN favorite INTEGER DEFAULT 0"
                )
            if "watched" not in cols:
                self._conn.execute(
                    "ALTER TABLE talks ADD COLUMN watched INTEGER DEFAULT 0"
                )
            if "resume_at" not in cols:
                self._conn.execute("ALTER TABLE talks ADD COLUMN resume_at REAL")
            if "total_time" not in cols:
                self._conn.execute("ALTER TABLE talks ADD COLUMN total_time REAL")
            self._conn.commit()
        except sqlite3.OperationalError:
            pass  # Read-only DB

    def close(self):
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
        return False

    # ------------------------------------------------------------------
    # Sync metadata
    # ------------------------------------------------------------------

    def get_sync_meta(self, key, default=None):
        row = self.conn.execute(
            "SELECT value FROM sync_meta WHERE key = ?", (key,)
        ).fetchone()
        return row["value"] if row else default

    def set_sync_meta(self, key, value):
        self.conn.execute(
            "INSERT OR REPLACE INTO sync_meta(key, value) VALUES (?, ?)",
            (key, str(value)),
        )
        self.conn.commit()

    def needs_sync(self, sync_key="last_full_sync", max_age_hours=12):
        """Return True if the given sync has never run or is older than max_age_hours."""
        last = self.get_sync_meta(sync_key)
        if last is None:
            return True
        try:
            last_ts = float(last)
        except (ValueError, TypeError):
            return True
        return (time.time() - last_ts) > (max_age_hours * 3600)

    def mark_synced(self, sync_key="last_full_sync"):
        self.set_sync_meta(sync_key, str(time.time()))

    # ------------------------------------------------------------------
    # Talk CRUD
    # ------------------------------------------------------------------

    def upsert_talk(self, talk):
        """
        Insert or update a talk. `talk` is a dict with keys matching
        the talks table columns. At minimum: object_id, slug, title.
        Speakers and topics are handled separately.
        """
        self.conn.execute(
            """INSERT INTO talks (object_id, slug, title, duration, thumb_url, api_rank,
                                  description, published_at, recorded_at, view_count,
                                  updated_at)
               VALUES (:object_id, :slug, :title, :duration, :thumb_url, :api_rank,
                       :description, :published_at, :recorded_at, :view_count,
                       strftime('%Y-%m-%dT%H:%M:%fZ','now'))
               ON CONFLICT(object_id) DO UPDATE SET
                   slug         = COALESCE(:slug, slug),
                   title        = COALESCE(:title, title),
                   duration     = COALESCE(:duration, duration),
                   thumb_url    = COALESCE(:thumb_url, thumb_url),
                   api_rank     = COALESCE(:api_rank, api_rank),
                   description  = COALESCE(:description, description),
                   published_at = COALESCE(:published_at, published_at),
                   recorded_at  = COALESCE(:recorded_at, recorded_at),
                   view_count   = COALESCE(:view_count, view_count),
                   updated_at   = strftime('%Y-%m-%dT%H:%M:%fZ','now')
            """,
            {
                "object_id": talk["object_id"],
                "slug": talk["slug"],
                "title": talk["title"],
                "duration": talk.get("duration"),
                "thumb_url": talk.get("thumb_url"),
                "api_rank": talk.get("api_rank"),
                "description": talk.get("description"),
                "published_at": talk.get("published_at"),
                "recorded_at": talk.get("recorded_at"),
                "view_count": talk.get("view_count"),
            },
        )

    def upsert_talks(self, talks):
        """Bulk upsert a list of talk dicts. Commits once at the end."""
        for talk in talks:
            self.upsert_talk(talk)
        self.conn.commit()

    def enrich_talk(self, slug, data):
        """
        Update enrichment fields for a talk identified by slug.
        `data` is a dict that may contain: description, published_at,
        recorded_at, view_count, topics (list of str), speakers (list of dict).
        """
        # Update talk fields
        self.conn.execute(
            """UPDATE talks SET
                   description  = COALESCE(:description, description),
                   published_at = COALESCE(:published_at, published_at),
                   recorded_at  = COALESCE(:recorded_at, recorded_at),
                   view_count   = COALESCE(:view_count, view_count),
                   enriched_at  = strftime('%Y-%m-%dT%H:%M:%fZ','now'),
                   updated_at   = strftime('%Y-%m-%dT%H:%M:%fZ','now')
               WHERE slug = :slug
            """,
            {
                "slug": slug,
                "description": data.get("description"),
                "published_at": data.get("published_at"),
                "recorded_at": data.get("recorded_at"),
                "view_count": data.get("view_count"),
            },
        )

        # Get the talk's object_id for junction tables
        row = self.conn.execute(
            "SELECT object_id FROM talks WHERE slug = ?", (slug,)
        ).fetchone()
        if not row:
            self.conn.commit()
            return
        talk_id = row["object_id"]

        # Update topics
        if "topics" in data and data["topics"]:
            self._set_talk_topics(talk_id, data["topics"])

        # Update speakers
        if "speakers" in data and data["speakers"]:
            self._set_talk_speakers(talk_id, data["speakers"])

        # Rebuild FTS entry for this talk
        self._rebuild_fts_for_talk(talk_id)

        self.conn.commit()

    def _set_talk_topics(self, talk_id, topics):
        """
        Replace all topics for a talk.
        `topics` can be a list of strings (names) or a list of dicts
        with keys: name, slug.
        """
        self.conn.execute("DELETE FROM talk_topics WHERE talk_id = ?", (talk_id,))
        for topic in topics:
            if isinstance(topic, str):
                topic = {"name": topic.strip(), "slug": ""}
            name = topic.get("name", "").strip()
            slug = topic.get("slug", "").strip()
            if not name:
                continue

            # Try to find existing topic by slug first, then by name
            topic_row = None
            if slug:
                topic_row = self.conn.execute(
                    "SELECT id FROM topics WHERE slug = ?", (slug,)
                ).fetchone()
            if not topic_row:
                topic_row = self.conn.execute(
                    "SELECT id FROM topics WHERE name = ? COLLATE NOCASE", (name,)
                ).fetchone()
            if not topic_row:
                # Create new topic — use IGNORE to handle races
                self.conn.execute(
                    "INSERT OR IGNORE INTO topics(name, slug) VALUES (?, ?)",
                    (name, slug),
                )
                # Re-lookup by slug or name
                if slug:
                    topic_row = self.conn.execute(
                        "SELECT id FROM topics WHERE slug = ?", (slug,)
                    ).fetchone()
                if not topic_row:
                    topic_row = self.conn.execute(
                        "SELECT id FROM topics WHERE name = ? COLLATE NOCASE", (name,)
                    ).fetchone()

            if topic_row:
                self.conn.execute(
                    "INSERT OR IGNORE INTO talk_topics(talk_id, topic_id) VALUES (?, ?)",
                    (talk_id, topic_row["id"]),
                )

    def _set_talk_speakers(self, talk_id, speakers):
        """
        Replace all speakers for a talk.
        `speakers` can be a list of strings (names) or a list of dicts
        with keys: name, slug, thumb_url, bio.
        """
        self.conn.execute("DELETE FROM talk_speakers WHERE talk_id = ?", (talk_id,))
        for spk in speakers:
            if isinstance(spk, str):
                spk = {"name": spk.strip()}
            name = spk.get("name", "").strip()
            if not name:
                continue
            # Upsert speaker
            self.conn.execute(
                """INSERT INTO speakers(name, slug, thumb_url, bio)
                   VALUES (:name, :slug, :thumb_url, :bio)
                   ON CONFLICT(name) DO UPDATE SET
                       slug      = COALESCE(:slug, slug),
                       thumb_url = COALESCE(:thumb_url, thumb_url),
                       bio       = COALESCE(:bio, bio)
                """,
                {
                    "name": name,
                    "slug": spk.get("slug"),
                    "thumb_url": spk.get("thumb_url"),
                    "bio": spk.get("bio"),
                },
            )
            spk_row = self.conn.execute(
                "SELECT id FROM speakers WHERE name = ?", (name,)
            ).fetchone()
            if spk_row:
                self.conn.execute(
                    "INSERT OR IGNORE INTO talk_speakers(talk_id, speaker_id) VALUES (?, ?)",
                    (talk_id, spk_row["id"]),
                )

    def _rebuild_fts_for_talk(self, talk_id):
        """Rebuild the FTS5 entry for a single talk, including speaker names."""
        row = self.conn.execute(
            "SELECT rowid, title, description FROM talks WHERE object_id = ?",
            (talk_id,),
        ).fetchone()
        if not row:
            return

        # Gather speaker names for this talk
        speaker_rows = self.conn.execute(
            """SELECT s.name FROM speakers s
               JOIN talk_speakers ts ON s.id = ts.speaker_id
               WHERE ts.talk_id = ?""",
            (talk_id,),
        ).fetchall()
        speakers_text = " ".join(r["name"] for r in speaker_rows)

        # Delete old FTS entry and insert new one
        self.conn.execute(
            "INSERT INTO talks_fts(talks_fts, rowid, title, description, speakers_text) "
            "VALUES ('delete', ?, ?, ?, ?)",
            (row["rowid"], row["title"], row["description"], ""),
        )
        self.conn.execute(
            "INSERT INTO talks_fts(rowid, title, description, speakers_text) VALUES (?, ?, ?, ?)",
            (row["rowid"], row["title"], row["description"], speakers_text),
        )

    def set_talk_speakers_from_api(self, talk_id, speakers_str):
        """
        Set speakers for a talk from the API's speakers string field.
        The API returns a single string like " Speaker Name" or
        "Speaker1, Speaker2".
        """
        if not speakers_str or not speakers_str.strip():
            return
        names = [n.strip() for n in speakers_str.split(",") if n.strip()]
        self._set_talk_speakers(talk_id, names)

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    def get_talk_count(self):
        row = self.conn.execute("SELECT COUNT(*) as cnt FROM talks").fetchone()
        return row["cnt"] if row else 0

    def get_newest(self, limit=24, offset=0):
        """Get talks ordered by newest first (api_rank, then published_at)."""
        return self.conn.execute(
            """SELECT t.*, GROUP_CONCAT(DISTINCT s.name) as speaker_names
               FROM talks t
               LEFT JOIN talk_speakers ts ON t.object_id = ts.talk_id
               LEFT JOIN speakers s ON ts.speaker_id = s.id
               GROUP BY t.object_id
               ORDER BY t.api_rank ASC, t.published_at DESC
               LIMIT ? OFFSET ?""",
            (limit, offset),
        ).fetchall()

    def get_talk_by_slug(self, slug):
        return self.conn.execute(
            """SELECT t.*, GROUP_CONCAT(DISTINCT s.name) as speaker_names
               FROM talks t
               LEFT JOIN talk_speakers ts ON t.object_id = ts.talk_id
               LEFT JOIN speakers s ON ts.speaker_id = s.id
               WHERE t.slug = ?
               GROUP BY t.object_id""",
            (slug,),
        ).fetchone()

    def search_talks(self, query, limit=50, offset=0):
        """Full-text search using FTS5 with BM25 ranking."""
        # FTS5 query: add * for prefix matching
        fts_query = " ".join(w + "*" for w in query.split() if w)
        if not fts_query:
            return []
        return self.conn.execute(
            """SELECT t.*, GROUP_CONCAT(DISTINCT s.name) as speaker_names,
                      rank
               FROM talks_fts fts
               JOIN talks t ON t.rowid = fts.rowid
               LEFT JOIN talk_speakers ts ON t.object_id = ts.talk_id
               LEFT JOIN speakers s ON ts.speaker_id = s.id
               WHERE talks_fts MATCH ?
               GROUP BY t.object_id
               ORDER BY rank
               LIMIT ? OFFSET ?""",
            (fts_query, limit, offset),
        ).fetchall()

    def get_topics(self):
        """Get all topics with talk counts, ordered by name."""
        return self.conn.execute(
            """SELECT t.id, t.name, t.slug, COUNT(tt.talk_id) as talk_count
               FROM topics t
               JOIN talk_topics tt ON t.id = tt.topic_id
               GROUP BY t.id
               ORDER BY t.name""",
        ).fetchall()

    def get_talks_by_topics(self, topic_names, limit=50, offset=0):
        """
        Get talks that match ALL of the given topic names (intersection).
        Works with one or more topic names.
        """
        if not topic_names:
            return []
        placeholders = ",".join("?" for _ in topic_names)
        return self.conn.execute(
            """SELECT t.*, GROUP_CONCAT(DISTINCT s.name) as speaker_names
               FROM talks t
               JOIN talk_topics tt ON t.object_id = tt.talk_id
               JOIN topics top ON tt.topic_id = top.id
               LEFT JOIN talk_speakers ts ON t.object_id = ts.talk_id
               LEFT JOIN speakers s ON ts.speaker_id = s.id
               WHERE top.name IN (%s)
               GROUP BY t.object_id
               HAVING COUNT(DISTINCT top.name) = ?
               ORDER BY t.api_rank ASC, t.published_at DESC
               LIMIT ? OFFSET ?"""
            % placeholders,
            (*topic_names, len(topic_names), limit, offset),
        ).fetchall()

    def count_talks_by_topics(self, topic_names):
        """Count talks matching ALL given topic names."""
        if not topic_names:
            return 0
        placeholders = ",".join("?" for _ in topic_names)
        row = self.conn.execute(
            """SELECT COUNT(*) as cnt FROM (
                   SELECT tt.talk_id
                   FROM talk_topics tt
                   JOIN topics top ON tt.topic_id = top.id
                   WHERE top.name IN (%s)
                   GROUP BY tt.talk_id
                   HAVING COUNT(DISTINCT top.name) = ?
               )"""
            % placeholders,
            (*topic_names, len(topic_names)),
        ).fetchone()
        return row["cnt"] if row else 0

    def get_related_topics(self, topic_names):
        """
        Given a list of selected topic names, return other topics that
        co-occur with ALL of them, along with talk counts.
        """
        if not topic_names:
            return self.get_topics()
        placeholders = ",".join("?" for _ in topic_names)
        return self.conn.execute(
            """SELECT top2.id, top2.name, top2.slug, COUNT(DISTINCT tt2.talk_id) as talk_count
               FROM talk_topics tt2
               JOIN topics top2 ON tt2.topic_id = top2.id
               WHERE tt2.talk_id IN (
                   SELECT tt.talk_id
                   FROM talk_topics tt
                   JOIN topics top ON tt.topic_id = top.id
                   WHERE top.name IN (%s)
                   GROUP BY tt.talk_id
                   HAVING COUNT(DISTINCT top.name) = ?
               )
               AND top2.name NOT IN (%s)
               GROUP BY top2.id
               HAVING talk_count > 0
               ORDER BY talk_count DESC"""
            % (placeholders, placeholders),
            (*topic_names, len(topic_names), *topic_names),
        ).fetchall()

    def get_speakers(self, limit=200, offset=0):
        """Get all speakers with talk counts, ordered by name."""
        return self.conn.execute(
            """SELECT s.id, s.name, s.slug, s.thumb_url, s.bio,
                      COUNT(ts.talk_id) as talk_count
               FROM speakers s
               JOIN talk_speakers ts ON s.id = ts.speaker_id
               GROUP BY s.id
               ORDER BY s.name
               LIMIT ? OFFSET ?""",
            (limit, offset),
        ).fetchall()

    def get_talks_by_speaker(self, speaker_name, limit=50, offset=0):
        """Get talks for a specific speaker, newest first."""
        return self.conn.execute(
            """SELECT t.*, GROUP_CONCAT(DISTINCT s2.name) as speaker_names
               FROM talks t
               JOIN talk_speakers ts ON t.object_id = ts.talk_id
               JOIN speakers s ON ts.speaker_id = s.id
               LEFT JOIN talk_speakers ts2 ON t.object_id = ts2.talk_id
               LEFT JOIN speakers s2 ON ts2.speaker_id = s2.id
               WHERE s.name = ?
               GROUP BY t.object_id
               ORDER BY t.api_rank ASC, t.published_at DESC
               LIMIT ? OFFSET ?""",
            (speaker_name, limit, offset),
        ).fetchall()

    # ------------------------------------------------------------------
    # Favorites
    # ------------------------------------------------------------------

    def set_favorite(self, slug, favorite=True):
        """Mark a talk as favorite (or unfavorite)."""
        self.conn.execute(
            "UPDATE talks SET favorite = ? WHERE slug = ?",
            (1 if favorite else 0, slug),
        )
        self.conn.commit()

    def get_favorites(self, limit=50, offset=0):
        """Get favorited talks."""
        return self.conn.execute(
            """SELECT t.*, GROUP_CONCAT(DISTINCT s.name) as speaker_names
               FROM talks t
               LEFT JOIN talk_speakers ts ON t.object_id = ts.talk_id
               LEFT JOIN speakers s ON ts.speaker_id = s.id
               WHERE t.favorite = 1
               GROUP BY t.object_id
               ORDER BY t.updated_at DESC
               LIMIT ? OFFSET ?""",
            (limit, offset),
        ).fetchall()

    def is_favorite(self, slug):
        """Check if a talk is favorited."""
        row = self.conn.execute(
            "SELECT favorite FROM talks WHERE slug = ?", (slug,)
        ).fetchone()
        return bool(row and row["favorite"])

    # ------------------------------------------------------------------
    # Playback tracking
    # ------------------------------------------------------------------

    def update_playback(self, slug, resume_at, total_time):
        """Update resume position for a talk."""
        self.conn.execute(
            "UPDATE talks SET resume_at = ?, total_time = ? WHERE slug = ?",
            (resume_at, total_time, slug),
        )
        self.conn.commit()

    def mark_watched(self, slug):
        """Mark a talk as watched and clear the resume point."""
        self.conn.execute(
            "UPDATE talks SET watched = 1, resume_at = NULL WHERE slug = ?",
            (slug,),
        )
        self.conn.commit()

    def mark_unwatched(self, slug):
        """Mark a talk as unwatched."""
        self.conn.execute(
            "UPDATE talks SET watched = 0, resume_at = NULL WHERE slug = ?",
            (slug,),
        )
        self.conn.commit()
