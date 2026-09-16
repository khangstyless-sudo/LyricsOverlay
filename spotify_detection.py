import os
import sys
import time
from datetime import datetime

import spotipy
from dotenv import load_dotenv
from spotipy.exceptions import SpotifyException
from spotipy.oauth2 import SpotifyOAuth


# How often Spotify should be checked.
POLL_INTERVAL_SECONDS = 2

# Permissions requested from the Spotify account.
SPOTIFY_SCOPES = (
    "user-read-currently-playing "
    "user-read-playback-state"
)


def milliseconds_to_time(milliseconds):
    """
    Convert milliseconds into a displayable MM:SS value.
    """

    if milliseconds is None:
        milliseconds = 0

    total_seconds = max(0, int(milliseconds / 1000))
    minutes, seconds = divmod(total_seconds, 60)

    return f"{minutes:02d}:{seconds:02d}"


def load_spotify_credentials():
    """
    Read Spotify credentials from the project's .env file.
    """

    load_dotenv()

    client_id = os.getenv("SPOTIPY_CLIENT_ID")
    client_secret = os.getenv("SPOTIPY_CLIENT_SECRET")
    redirect_uri = os.getenv("SPOTIPY_REDIRECT_URI")

    missing_variables = []

    if not client_id:
        missing_variables.append("SPOTIPY_CLIENT_ID")

    if not client_secret:
        missing_variables.append("SPOTIPY_CLIENT_SECRET")

    if not redirect_uri:
        missing_variables.append("SPOTIPY_REDIRECT_URI")

    if missing_variables:
        print("Missing Spotify configuration:")
        for variable in missing_variables:
            print(f"  - {variable}")

        print("\nCreate a .env file beside this Python script.")
        sys.exit(1)

    return client_id, client_secret, redirect_uri


def create_spotify_client():
    """
    Create an authenticated Spotipy client.

    Spotipy stores the access and refresh tokens in .spotify_cache.
    The browser login is normally required only on the first run.
    """

    client_id, client_secret, redirect_uri = load_spotify_credentials()

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


def get_current_playback(spotify):
    """
    Retrieve and simplify Spotify's currently playing information.

    Returns:
        A dictionary containing the playback state, or None when
        Spotify has no active playback session.
    """

    response = spotify.current_user_playing_track()

    if not response:
        return None

    item = response.get("item")

    if not item:
        return None

    # Spotify can return tracks or podcast episodes.
    item_type = item.get("type", "unknown")

    if item_type == "track":
        artists = [
            artist.get("name", "Unknown artist")
            for artist in item.get("artists", [])
        ]

        artist_name = ", ".join(artists) or "Unknown artist"
        album = item.get("album") or {}
        album_name = album.get("name", "Unknown album")

    elif item_type == "episode":
        show = item.get("show") or {}
        artist_name = show.get("name", "Unknown podcast")
        album_name = "Podcast episode"

    else:
        artist_name = "Unknown artist"
        album_name = "Unknown album"

    return {
        "id": item.get("id") or item.get("uri") or item.get("name"),
        "uri": item.get("uri"),
        "type": item_type,
        "title": item.get("name", "Unknown title"),
        "artist": artist_name,
        "album": album_name,
        "duration_ms": item.get("duration_ms", 0),
        "progress_ms": response.get("progress_ms", 0),
        "is_playing": response.get("is_playing", False),
        "timestamp": response.get("timestamp"),
    }


def print_track_information(playback):
    """
    Print full information when a new track is detected.
    """

    status = "Playing" if playback["is_playing"] else "Paused"

    print("\n" + "=" * 64)
    print("NEW SPOTIFY ITEM DETECTED")
    print("=" * 64)
    print(f"Title:      {playback['title']}")
    print(f"Artist:     {playback['artist']}")
    print(f"Album:      {playback['album']}")
    print(f"Type:       {playback['type']}")
    print(f"Status:     {status}")
    print(
        "Position:   "
        f"{milliseconds_to_time(playback['progress_ms'])} / "
        f"{milliseconds_to_time(playback['duration_ms'])}"
    )
    print(f"Spotify ID: {playback['id']}")
    print(f"Spotify URI:{playback['uri'] or ' Not available'}")
    print("=" * 64)


def run_detection_loop(spotify):
    """
    Continuously monitor Spotify playback.
    """

    previous_track_id = None
    previous_is_playing = None
    previous_progress_ms = None
    previously_inactive = False

    print("\nSpotify playback detector is running.")
    print("Start playing something in Spotify.")
    print("Press Ctrl+C to stop.\n")

    while True:
        request_started = time.monotonic()

        try:
            playback = get_current_playback(spotify)

        except SpotifyException as error:
            if error.http_status == 429:
                retry_after = int(
                    error.headers.get("Retry-After", POLL_INTERVAL_SECONDS)
                )

                print(
                    f"\nSpotify rate limit reached. "
                    f"Waiting {retry_after} seconds."
                )
                time.sleep(retry_after)
                continue

            if error.http_status == 401:
                print("\nSpotify authorization failed or expired.")
                print("Delete .spotify_cache and run the program again.")
                break

            if error.http_status == 403:
                print("\nSpotify rejected the requested permission.")
                print(
                    "Check your app settings and Spotify account access."
                )
                break

            print(
                f"\nSpotify API error "
                f"{error.http_status}: {error.msg}"
            )
            time.sleep(5)
            continue

        except Exception as error:
            print(f"\nConnection error: {error}")
            print("Will try again in five seconds.")
            time.sleep(5)
            continue

        request_finished = time.monotonic()
        request_duration_ms = int(
            (request_finished - request_started) * 1000
        )

        if playback is None:
            if not previously_inactive:
                print(
                    "\nNo active Spotify playback was detected. "
                    "Play a song and leave Spotify running."
                )
                previously_inactive = True

            previous_track_id = None
            previous_is_playing = None
            previous_progress_ms = None

            time.sleep(5)
            continue

        previously_inactive = False

        track_changed = playback["id"] != previous_track_id

        if track_changed:
            print_track_information(playback)

        elif playback["is_playing"] != previous_is_playing:
            if playback["is_playing"]:
                print("\nPlayback resumed.")
            else:
                print("\nPlayback paused.")

        # A large unexpected difference usually means that the user
        # moved Spotify's playback slider.
        if (
            not track_changed
            and playback["is_playing"]
            and previous_is_playing
            and previous_progress_ms is not None
        ):
            expected_progress_ms = (
                previous_progress_ms
                + POLL_INTERVAL_SECONDS * 1000
            )

            difference_ms = abs(
                playback["progress_ms"] - expected_progress_ms
            )

            if difference_ms > 4000:
                print(
                    "\nSeek detected. New position: "
                    f"{milliseconds_to_time(playback['progress_ms'])}"
                )

        current_time = datetime.now().strftime("%H:%M:%S")
        status_symbol = "PLAY" if playback["is_playing"] else "PAUSE"

        position = milliseconds_to_time(playback["progress_ms"])
        duration = milliseconds_to_time(playback["duration_ms"])

        status_line = (
            f"\r[{current_time}] "
            f"{status_symbol:<5} | "
            f"{position} / {duration} | "
            f"API {request_duration_ms:>4} ms | "
            f"{playback['artist']} - {playback['title']}"
        )

        # Padding clears remnants of the previous longer line.
        print(status_line.ljust(150), end="", flush=True)

        previous_track_id = playback["id"]
        previous_is_playing = playback["is_playing"]
        previous_progress_ms = playback["progress_ms"]

        time.sleep(POLL_INTERVAL_SECONDS)


def main():
    print("=" * 64)
    print("SPOTIFY CURRENT PLAYBACK DETECTOR")
    print("=" * 64)

    try:
        spotify = create_spotify_client()
        run_detection_loop(spotify)

    except KeyboardInterrupt:
        print("\n\nPlayback detector stopped.")

    except SpotifyException as error:
        print("\nUnable to connect to Spotify.")
        print(f"Spotify error: {error}")

    except Exception as error:
        print("\nUnexpected startup error.")
        print(f"Details: {error}")


if __name__ == "__main__":
    main()