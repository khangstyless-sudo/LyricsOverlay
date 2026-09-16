import sqlite3
from datetime import datetime
from pathlib import Path


BASE_DIRECTORY = Path(__file__).resolve().parent
DEFAULT_DATABASE_PATH = BASE_DIRECTORY / "lyrics.db"


class LyricsDatabase:
    def __init__(self, database_path=DEFAULT_DATABASE_PATH):
        self.database_path = Path(database_path)
        self.initialize_database()

    def connect(self):
        """
        Create a new SQLite connection.

        A new connection is opened for each operation. This is convenient
        when the Spotify worker and user interface later run on separate
        threads.
        """

        connection = sqlite3.connect(self.database_path)

        connection.row_factory = sqlite3.Row

        connection.execute(
            "PRAGMA foreign_keys = ON"
        )

        return connection

    def initialize_database(self):
        """
        Create the required tables if they do not already exist.
        """

        with self.connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS tracks (
                    spotify_track_id TEXT PRIMARY KEY,
                    spotify_uri TEXT,
                    title TEXT NOT NULL,
                    artist TEXT NOT NULL,
                    album TEXT,
                    duration_ms INTEGER NOT NULL DEFAULT 0,
                    synced_lyrics TEXT NOT NULL,
                    lyrics_offset_ms INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )

            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_tracks_title_artist
                ON tracks(title, artist)
                """
            )

            connection.commit()

    def add_or_update_track(
        self,
        spotify_track_id,
        spotify_uri,
        title,
        artist,
        album,
        duration_ms,
        synced_lyrics,
        lyrics_offset_ms=0,
    ):
        """
        Add a new track or update an existing track.

        Spotify track ID is the unique database key.
        """

        current_time = datetime.now().isoformat(
            timespec="seconds"
        )

        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO tracks (
                    spotify_track_id,
                    spotify_uri,
                    title,
                    artist,
                    album,
                    duration_ms,
                    synced_lyrics,
                    lyrics_offset_ms,
                    created_at,
                    updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)

                ON CONFLICT(spotify_track_id)
                DO UPDATE SET
                    spotify_uri = excluded.spotify_uri,
                    title = excluded.title,
                    artist = excluded.artist,
                    album = excluded.album,
                    duration_ms = excluded.duration_ms,
                    synced_lyrics = excluded.synced_lyrics,
                    lyrics_offset_ms = excluded.lyrics_offset_ms,
                    updated_at = excluded.updated_at
                """,
                (
                    spotify_track_id,
                    spotify_uri,
                    title,
                    artist,
                    album,
                    duration_ms,
                    synced_lyrics,
                    lyrics_offset_ms,
                    current_time,
                    current_time,
                ),
            )

            connection.commit()

    def get_track_by_spotify_id(self, spotify_track_id):
        """
        Find a track using its Spotify track ID.

        Returns a dictionary or None.
        """

        with self.connect() as connection:
            row = connection.execute(
                """
                SELECT *
                FROM tracks
                WHERE spotify_track_id = ?
                """,
                (spotify_track_id,),
            ).fetchone()

        if row is None:
            return None

        return dict(row)

    def search_tracks(self, search_text):
        """
        Search by title, artist or album.
        """

        search_pattern = f"%{search_text}%"

        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT
                    spotify_track_id,
                    title,
                    artist,
                    album,
                    duration_ms,
                    lyrics_offset_ms,
                    updated_at
                FROM tracks
                WHERE title LIKE ?
                   OR artist LIKE ?
                   OR album LIKE ?
                ORDER BY artist, title
                """,
                (
                    search_pattern,
                    search_pattern,
                    search_pattern,
                ),
            ).fetchall()

        return [dict(row) for row in rows]

    def get_all_tracks(self):
        """
        Return all locally stored tracks.
        """

        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT
                    spotify_track_id,
                    title,
                    artist,
                    album,
                    duration_ms,
                    lyrics_offset_ms,
                    updated_at
                FROM tracks
                ORDER BY artist, title
                """
            ).fetchall()

        return [dict(row) for row in rows]

    def update_lyrics_offset(
        self,
        spotify_track_id,
        lyrics_offset_ms,
    ):
        """
        Set a synchronization offset for one recording.
        """

        current_time = datetime.now().isoformat(
            timespec="seconds"
        )

        with self.connect() as connection:
            cursor = connection.execute(
                """
                UPDATE tracks
                SET lyrics_offset_ms = ?,
                    updated_at = ?
                WHERE spotify_track_id = ?
                """,
                (
                    lyrics_offset_ms,
                    current_time,
                    spotify_track_id,
                ),
            )

            connection.commit()

        return cursor.rowcount > 0

    def delete_track(self, spotify_track_id):
        """
        Delete one track from the local database.
        """

        with self.connect() as connection:
            cursor = connection.execute(
                """
                DELETE FROM tracks
                WHERE spotify_track_id = ?
                """,
                (spotify_track_id,),
            )

            connection.commit()

        return cursor.rowcount > 0