import os
import sys

import spotipy
from dotenv import load_dotenv
from spotipy.oauth2 import SpotifyOAuth

from lyrics_database import LyricsDatabase


SPOTIFY_SCOPES = (
    "user-read-currently-playing "
    "user-read-playback-state"
)


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
        print("Spotify credentials are missing from .env.")
        sys.exit(1)

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


def get_current_track(spotify):
    playback = spotify.current_user_playing_track()

    if not playback:
        return None

    item = playback.get("item")

    if not item:
        return None

    if item.get("type") != "track":
        return None

    artists = item.get("artists", [])

    artist_names = [
        artist.get("name", "Unknown artist")
        for artist in artists
    ]

    album = item.get("album") or {}

    return {
        "spotify_track_id": item.get("id"),
        "spotify_uri": item.get("uri"),
        "title": item.get("name", "Unknown title"),
        "artist": ", ".join(artist_names),
        "album": album.get("name", ""),
        "duration_ms": item.get("duration_ms", 0),
    }


def milliseconds_to_time(milliseconds):
    total_seconds = int(milliseconds / 1000)
    minutes, seconds = divmod(total_seconds, 60)

    return f"{minutes:02d}:{seconds:02d}"


def collect_multiline_lyrics():
    """
    Read lyrics from the terminal until the user enters ::SAVE.
    """

    print("\nPaste your synchronized LRC lyrics below.")
    print("When finished, type ::SAVE on a new line.")
    print("Type ::CANCEL to cancel.\n")

    lines = []

    while True:
        try:
            line = input()

        except EOFError:
            break

        command = line.strip().upper()

        if command == "::SAVE":
            break

        if command == "::CANCEL":
            return None

        lines.append(line)

    lyrics = "\n".join(lines).strip()

    if lyrics:
        lyrics += "\n"

    return lyrics


def main():
    spotify = create_spotify_client()
    database = LyricsDatabase()

    print("Checking the currently playing Spotify track...")

    track = get_current_track(spotify)

    if track is None:
        print(
            "No supported Spotify track is currently active.\n"
            "Start playing a song and run this program again."
        )
        return

    print("\nCurrently playing:")
    print(f"Title:    {track['title']}")
    print(f"Artist:   {track['artist']}")
    print(f"Album:    {track['album']}")
    print(
        "Duration: "
        f"{milliseconds_to_time(track['duration_ms'])}"
    )
    print(
        "Spotify ID: "
        f"{track['spotify_track_id']}"
    )

    existing_track = database.get_track_by_spotify_id(
        track["spotify_track_id"]
    )

    if existing_track:
        print(
            "\nThis recording already exists in the database."
        )
        print(
            "Saving new lyrics will replace its existing lyrics."
        )

    lyrics = collect_multiline_lyrics()

    if lyrics is None:
        print("\nImport cancelled.")
        return

    if not lyrics:
        print("\nNo lyrics were entered. Nothing was saved.")
        return

    database.add_or_update_track(
        spotify_track_id=track["spotify_track_id"],
        spotify_uri=track["spotify_uri"],
        title=track["title"],
        artist=track["artist"],
        album=track["album"],
        duration_ms=track["duration_ms"],
        synced_lyrics=lyrics,
        lyrics_offset_ms=0,
    )

    print("\nLyrics saved successfully.")
    print(
        f"Database key: {track['spotify_track_id']}"
    )


if __name__ == "__main__":
    main()