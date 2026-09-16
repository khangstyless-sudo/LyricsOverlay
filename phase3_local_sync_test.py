import os
import sys
import time

import spotipy
from dotenv import load_dotenv
from spotipy.oauth2 import SpotifyOAuth

from lrc_parser import (
    find_active_line_index,
    get_display_lines,
    parse_lrc,
)
from lyrics_database import LyricsDatabase


SPOTIFY_SCOPES = (
    "user-read-currently-playing "
    "user-read-playback-state"
)

POLL_INTERVAL_SECONDS = 1


def create_spotify_client():
    load_dotenv()

    client_id = os.getenv("SPOTIPY_CLIENT_ID")
    client_secret = os.getenv("SPOTIPY_CLIENT_SECRET")
    redirect_uri = os.getenv("SPOTIPY_REDIRECT_URI")

    if not all(
        [
            client_id,
            client_secret,
            redirect_uri,
        ]
    ):
        print("Spotify credentials are missing.")
        sys.exit(1)

    return spotipy.Spotify(
        auth_manager=SpotifyOAuth(
            client_id=client_id,
            client_secret=client_secret,
            redirect_uri=redirect_uri,
            scope=SPOTIFY_SCOPES,
            cache_path=".spotify_cache",
            open_browser=True,
        ),
        requests_timeout=10,
        retries=3,
    )


def get_playback_state(spotify):
    response = spotify.current_user_playing_track()

    if not response:
        return None

    item = response.get("item")

    if not item or item.get("type") != "track":
        return None

    artists = [
        artist.get("name", "Unknown artist")
        for artist in item.get("artists", [])
    ]

    return {
        "spotify_track_id": item.get("id"),
        "title": item.get("name", "Unknown title"),
        "artist": ", ".join(artists),
        "progress_ms": response.get("progress_ms", 0),
        "duration_ms": item.get("duration_ms", 0),
        "is_playing": response.get("is_playing", False),
    }


def main():
    spotify = create_spotify_client()
    database = LyricsDatabase()

    previous_track_id = None
    previous_line_index = None
    parsed_lyrics = []
    stored_track = None

    print("Local lyrics synchronization test started.")
    print("Press Ctrl+C to stop.\n")

    try:
        while True:
            playback = get_playback_state(spotify)

            if playback is None:
                print(
                    "\rNo active Spotify track."
                    .ljust(100),
                    end="",
                    flush=True,
                )

                time.sleep(3)
                continue

            track_changed = (
                playback["spotify_track_id"]
                != previous_track_id
            )

            if track_changed:
                print("\n\nNew Spotify track:")
                print(f"Title:  {playback['title']}")
                print(f"Artist: {playback['artist']}")
                print(
                    "ID:     "
                    f"{playback['spotify_track_id']}"
                )

                stored_track = (
                    database.get_track_by_spotify_id(
                        playback["spotify_track_id"]
                    )
                )

                if stored_track is None:
                    print(
                        "Result: Lyrics have not been added "
                        "to the local database."
                    )

                    parsed_lyrics = []

                else:
                    parsed_lyrics = parse_lrc(
                        stored_track["synced_lyrics"]
                    )

                    print("Result: Local lyrics found.")
                    print(
                        f"Parsed lines: "
                        f"{len(parsed_lyrics)}"
                    )
                    print(
                        "Track offset: "
                        f"{stored_track['lyrics_offset_ms']} ms"
                    )

                previous_track_id = (
                    playback["spotify_track_id"]
                )

                previous_line_index = None

            if stored_track and parsed_lyrics:
                offset_ms = stored_track[
                    "lyrics_offset_ms"
                ]

                adjusted_position_ms = (
                    playback["progress_ms"]
                    + offset_ms
                )

                active_index = find_active_line_index(
                    parsed_lyrics,
                    adjusted_position_ms,
                )

                if active_index != previous_line_index:
                    lines = get_display_lines(
                        parsed_lyrics,
                        active_index,
                    )

                    print("\n")
                    print(
                        f"Previous: {lines['previous']}"
                    )
                    print(
                        f"CURRENT:  {lines['current']}"
                    )
                    print(
                        f"Next:     {lines['next']}"
                    )

                    previous_line_index = active_index

            time.sleep(POLL_INTERVAL_SECONDS)

    except KeyboardInterrupt:
        print("\n\nLocal synchronization test stopped.")


if __name__ == "__main__":
    main()