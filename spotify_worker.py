import os
import time

import spotipy
from dotenv import load_dotenv
from PySide6.QtCore import QObject, Signal, Slot
from spotipy.exceptions import SpotifyException
from spotipy.oauth2 import SpotifyOAuth


SPOTIFY_SCOPES = (
    "user-read-currently-playing "
    "user-read-playback-state"
)


class SpotifyWorker(QObject):
    """
    Poll Spotify from a background QThread.

    Results are sent safely to the main UI thread through Qt signals.
    """

    playback_updated = Signal(dict)
    playback_inactive = Signal()
    connection_error = Signal(str)
    finished = Signal()

    def __init__(
        self,
        poll_interval_seconds=2.0,
        inactive_interval_seconds=4.0,
    ):
        super().__init__()

        self.poll_interval_seconds = (
            poll_interval_seconds
        )

        self.inactive_interval_seconds = (
            inactive_interval_seconds
        )

        self.running = False
        self.spotify = None

    def create_spotify_client(self):
        load_dotenv()

        client_id = os.getenv("SPOTIPY_CLIENT_ID")
        client_secret = os.getenv(
            "SPOTIPY_CLIENT_SECRET"
        )
        redirect_uri = os.getenv(
            "SPOTIPY_REDIRECT_URI"
        )

        if not all(
            [
                client_id,
                client_secret,
                redirect_uri,
            ]
        ):
            raise RuntimeError(
                "Spotify credentials are missing from .env."
            )

        authentication_manager = SpotifyOAuth(
            client_id=client_id,
            client_secret=client_secret,
            redirect_uri=redirect_uri,
            scope=SPOTIFY_SCOPES,
            cache_path=".spotify_cache",
            open_browser=True,
        )

        return spotipy.Spotify(
            auth_manager=authentication_manager,
            requests_timeout=10,
            retries=3,
        )

    def get_current_playback(self):
        request_started = time.monotonic()

        response = (
            self.spotify.current_user_playing_track()
        )

        request_finished = time.monotonic()

        request_duration_ms = int(
            (
                request_finished
                - request_started
            )
            * 1000
        )

        if not response:
            return None

        item = response.get("item")

        if not item:
            return None

        if item.get("type") != "track":
            return None

        artist_names = [
            artist.get("name", "Unknown artist")
            for artist in item.get("artists", [])
        ]

        album = item.get("album") or {}

        return {
            "spotify_track_id": item.get("id"),
            "spotify_uri": item.get("uri"),
            "title": item.get(
                "name",
                "Unknown title",
            ),
            "artist": ", ".join(artist_names),
            "album": album.get("name", ""),
            "duration_ms": item.get(
                "duration_ms",
                0,
            ),
            "progress_ms": response.get(
                "progress_ms",
                0,
            ),
            "is_playing": response.get(
                "is_playing",
                False,
            ),
            "request_duration_ms": (
                request_duration_ms
            ),
        }

    @Slot()
    def run(self):
        """
        Start the blocking Spotify polling loop.

        This method must run inside a QThread.
        """

        self.running = True

        try:
            self.spotify = (
                self.create_spotify_client()
            )

        except Exception as error:
            self.connection_error.emit(str(error))
            self.finished.emit()
            return

        while self.running:
            try:
                playback = self.get_current_playback()

                if playback is None:
                    self.playback_inactive.emit()
                    self.interruptible_sleep(
                        self.inactive_interval_seconds
                    )
                    continue

                self.playback_updated.emit(playback)

                self.interruptible_sleep(
                    self.poll_interval_seconds
                )

            except SpotifyException as error:
                if error.http_status == 429:
                    retry_after = int(
                        error.headers.get(
                            "Retry-After",
                            5,
                        )
                    )

                    self.connection_error.emit(
                        "Spotify rate limit reached. "
                        f"Retrying after {retry_after} "
                        "seconds."
                    )

                    self.interruptible_sleep(
                        retry_after
                    )
                    continue

                self.connection_error.emit(
                    "Spotify API error "
                    f"{error.http_status}: "
                    f"{error.msg}"
                )

                self.interruptible_sleep(5)

            except Exception as error:
                self.connection_error.emit(
                    f"Spotify connection error: {error}"
                )

                self.interruptible_sleep(5)

        self.finished.emit()

    def interruptible_sleep(self, seconds):
        """
        Sleep in short intervals so the worker can stop quickly.
        """

        end_time = time.monotonic() + seconds

        while (
            self.running
            and time.monotonic() < end_time
        ):
            time.sleep(0.1)

    @Slot()
    def stop(self):
        self.running = False