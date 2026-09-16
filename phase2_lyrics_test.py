import json
import os
import re
import sys
import time
from bisect import bisect_right
from pathlib import Path

import requests
import spotipy
from dotenv import load_dotenv
from spotipy.exceptions import SpotifyException
from spotipy.oauth2 import SpotifyOAuth


# -------------------------------------------------------------------
# Application configuration
# -------------------------------------------------------------------

SPOTIFY_POLL_INTERVAL = 2
INACTIVE_POLL_INTERVAL = 5

SPOTIFY_SCOPES = (
    "user-read-currently-playing "
    "user-read-playback-state"
)

LRCLIB_API_URL = "https://lrclib.net/api/get"

# LRCLIB asks applications to identify themselves.
# Replace the email with your own address if you wish.
LRCLIB_USER_AGENT = (
    "SpotifyLyricsOverlay/0.2 "
    "(Python desktop application; contact: your-email@example.com)"
)

BASE_DIRECTORY = Path(__file__).resolve().parent
CACHE_DIRECTORY = BASE_DIRECTORY / "lyrics_cache"

# Supports timestamps such as:
# [00:12]
# [00:12.34]
# [01:05.123]
LRC_TIMESTAMP_PATTERN = re.compile(
    r"\[(?P<minutes>\d{1,3}):"
    r"(?P<seconds>\d{2})"
    r"(?:\.(?P<fraction>\d{1,3}))?\]"
)


# -------------------------------------------------------------------
# General formatting functions
# -------------------------------------------------------------------

def milliseconds_to_time(milliseconds):
    """
    Convert milliseconds to MM:SS.
    """

    if milliseconds is None:
        milliseconds = 0

    total_seconds = max(0, int(milliseconds / 1000))
    minutes, seconds = divmod(total_seconds, 60)

    return f"{minutes:02d}:{seconds:02d}"


def format_lrc_timestamp(milliseconds):
    """
    Convert milliseconds to a timestamp useful for debugging.
    """

    milliseconds = max(0, int(milliseconds))

    minutes = milliseconds // 60_000
    remaining = milliseconds % 60_000
    seconds = remaining // 1_000
    fraction = remaining % 1_000

    return f"{minutes:02d}:{seconds:02d}.{fraction:03d}"


# -------------------------------------------------------------------
# Spotify authentication
# -------------------------------------------------------------------

def load_spotify_credentials():
    """
    Load Spotify credentials from the .env file.
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

        print("\nCheck the .env file beside this script.")
        sys.exit(1)

    return client_id, client_secret, redirect_uri


def create_spotify_client():
    """
    Create the authenticated Spotify client.
    """

    client_id, client_secret, redirect_uri = load_spotify_credentials()

    authentication_manager = SpotifyOAuth(
        client_id=client_id,
        client_secret=client_secret,
        redirect_uri=redirect_uri,
        scope=SPOTIFY_SCOPES,
        cache_path=str(BASE_DIRECTORY / ".spotify_cache"),
        open_browser=True,
    )

    return spotipy.Spotify(
        auth_manager=authentication_manager,
        requests_timeout=10,
        retries=3,
    )


# -------------------------------------------------------------------
# Spotify playback retrieval
# -------------------------------------------------------------------

def get_current_track(spotify):
    """
    Return simplified information about the current Spotify track.

    Podcast episodes are ignored in this phase because their text
    sources and synchronization requirements differ from music.
    """

    response = spotify.current_user_playing_track()

    if not response:
        return None

    item = response.get("item")

    if not item:
        return None

    if item.get("type") != "track":
        return {
            "unsupported_type": item.get("type", "unknown"),
            "title": item.get("name", "Unknown item"),
        }

    artists = item.get("artists", [])

    artist_names = [
        artist.get("name", "Unknown artist")
        for artist in artists
    ]

    primary_artist = (
        artist_names[0]
        if artist_names
        else "Unknown artist"
    )

    album = item.get("album") or {}

    return {
        "unsupported_type": None,
        "id": item.get("id"),
        "uri": item.get("uri"),
        "title": item.get("name", "Unknown title"),
        "primary_artist": primary_artist,
        "all_artists": artist_names,
        "album": album.get("name", ""),
        "duration_ms": item.get("duration_ms", 0),
        "progress_ms": response.get("progress_ms", 0),
        "is_playing": response.get("is_playing", False),
    }


# -------------------------------------------------------------------
# Local lyrics caching
# -------------------------------------------------------------------

def safe_filename(value):
    """
    Remove characters that Windows does not allow in filenames.
    """

    value = re.sub(r'[<>:"/\\|?*]', "_", value)
    value = value.strip(" .")

    return value[:100] or "unknown"


def get_cache_path(track):
    """
    Build a stable cache filename.

    Spotify track ID is used because it distinguishes different
    recordings, releases, and remasters more reliably than title alone.
    """

    spotify_id = track.get("id") or "unknown"
    title = safe_filename(track.get("title", "unknown"))

    filename = f"{spotify_id}_{title}.json"

    return CACHE_DIRECTORY / filename


def load_cached_lyrics(track):
    """
    Load lyrics from disk when previously fetched.
    """

    cache_path = get_cache_path(track)

    if not cache_path.exists():
        return None

    try:
        with cache_path.open(
            "r",
            encoding="utf-8"
        ) as cache_file:
            cache_data = json.load(cache_file)

        print(f"Loaded lyrics from cache: {cache_path.name}")
        return cache_data

    except (OSError, json.JSONDecodeError) as error:
        print(f"Could not read cached lyrics: {error}")
        return None


def save_lyrics_to_cache(track, lyrics_result):
    """
    Save the lyrics lookup result as UTF-8 JSON.
    """

    CACHE_DIRECTORY.mkdir(parents=True, exist_ok=True)

    cache_path = get_cache_path(track)

    cache_data = {
        "spotify": {
            "id": track.get("id"),
            "uri": track.get("uri"),
            "title": track.get("title"),
            "primary_artist": track.get("primary_artist"),
            "all_artists": track.get("all_artists"),
            "album": track.get("album"),
            "duration_ms": track.get("duration_ms"),
        },
        "lyrics": lyrics_result,
    }

    try:
        with cache_path.open(
            "w",
            encoding="utf-8"
        ) as cache_file:
            json.dump(
                cache_data,
                cache_file,
                ensure_ascii=False,
                indent=2,
            )

        print(f"Saved lyrics cache: {cache_path.name}")

    except OSError as error:
        print(f"Could not save lyrics cache: {error}")

    return cache_data


# -------------------------------------------------------------------
# LRCLIB lyrics retrieval
# -------------------------------------------------------------------

def request_lyrics_from_lrclib(track):
    """
    Request lyrics from LRCLIB using Spotify metadata.

    Returns:
        Dictionary when a match is found.
        None when no suitable match is found.
    """

    parameters = {
        "track_name": track["title"],
        "artist_name": track["primary_artist"],
        "album_name": track["album"],
        "duration": round(track["duration_ms"] / 1000),
    }

    headers = {
        "User-Agent": LRCLIB_USER_AGENT,
        "Accept": "application/json",
    }

    print("\nSearching LRCLIB with:")
    print(f"  Track:    {parameters['track_name']}")
    print(f"  Artist:   {parameters['artist_name']}")
    print(f"  Album:    {parameters['album_name']}")
    print(f"  Duration: {parameters['duration']} seconds")

    try:
        response = requests.get(
            LRCLIB_API_URL,
            params=parameters,
            headers=headers,
            timeout=15,
        )

    except requests.exceptions.SSLError as error:
        print("\nLRCLIB SSL verification failed.")
        print("Check pip-system-certs and your corporate certificate setup.")
        print(f"Technical detail: {error}")
        return None

    except requests.exceptions.Timeout:
        print("\nLRCLIB request timed out.")
        return None

    except requests.exceptions.ConnectionError as error:
        print("\nCould not connect to LRCLIB.")
        print(f"Technical detail: {error}")
        return None

    except requests.exceptions.RequestException as error:
        print("\nUnexpected LRCLIB request error.")
        print(f"Technical detail: {error}")
        return None

    if response.status_code == 404:
        print("No matching lyrics were found.")
        return None

    if response.status_code == 429:
        retry_after = response.headers.get("Retry-After", "unknown")

        print("LRCLIB rate limit reached.")
        print(f"Retry after: {retry_after} seconds")
        return None

    if response.status_code >= 500:
        print(
            "LRCLIB is temporarily unavailable. "
            f"HTTP status: {response.status_code}"
        )
        return None

    try:
        response.raise_for_status()
        result = response.json()

    except requests.exceptions.HTTPError as error:
        print(f"LRCLIB returned an HTTP error: {error}")
        return None

    except requests.exceptions.JSONDecodeError:
        print("LRCLIB returned invalid JSON.")
        return None

    return result


def get_lyrics_for_track(track):
    """
    Check the cache before requesting lyrics from LRCLIB.
    """

    cached_result = load_cached_lyrics(track)

    if cached_result is not None:
        return cached_result

    lyrics_result = request_lyrics_from_lrclib(track)

    if lyrics_result is None:
        return None

    return save_lyrics_to_cache(track, lyrics_result)


# -------------------------------------------------------------------
# LRC timestamp parsing
# -------------------------------------------------------------------

def fraction_to_milliseconds(fraction):
    """
    Convert an LRC fractional second value into milliseconds.

    Examples:
        "1"   becomes 100 ms
        "12"  becomes 120 ms
        "123" becomes 123 ms
    """

    if not fraction:
        return 0

    normalized = fraction.ljust(3, "0")[:3]

    return int(normalized)


def parse_lrc(synced_lyrics):
    """
    Convert synchronized LRC text into a sorted list.

    Returned structure:

        [
            {
                "timestamp_ms": 12400,
                "text": "First line"
            },
            ...
        ]

    Multiple timestamps on the same lyric line are supported.
    """

    parsed_lines = []

    if not synced_lyrics:
        return parsed_lines

    for raw_line in synced_lyrics.splitlines():
        timestamp_matches = list(
            LRC_TIMESTAMP_PATTERN.finditer(raw_line)
        )

        if not timestamp_matches:
            continue

        lyric_text = LRC_TIMESTAMP_PATTERN.sub(
            "",
            raw_line
        ).strip()

        for timestamp_match in timestamp_matches:
            minutes = int(
                timestamp_match.group("minutes")
            )

            seconds = int(
                timestamp_match.group("seconds")
            )

            fraction = timestamp_match.group("fraction")
            milliseconds = fraction_to_milliseconds(fraction)

            timestamp_ms = (
                minutes * 60_000
                + seconds * 1_000
                + milliseconds
            )

            parsed_lines.append({
                "timestamp_ms": timestamp_ms,
                "text": lyric_text,
            })

    parsed_lines.sort(
        key=lambda line: line["timestamp_ms"]
    )

    return parsed_lines


def find_active_lyric_index(parsed_lyrics, playback_position_ms):
    """
    Find the currently active lyric using binary search.

    Returns -1 when playback is before the first lyric.
    """

    if not parsed_lyrics:
        return -1

    timestamps = [
        line["timestamp_ms"]
        for line in parsed_lyrics
    ]

    return bisect_right(
        timestamps,
        playback_position_ms
    ) - 1


# -------------------------------------------------------------------
# Test-result display
# -------------------------------------------------------------------

def print_lyrics_summary(track, cache_data):
    """
    Print metadata and synchronization results without dumping
    the complete lyric text into the terminal.
    """

    lyrics = cache_data.get("lyrics", {})

    matched_title = lyrics.get("trackName", "Unknown")
    matched_artist = lyrics.get("artistName", "Unknown")
    matched_album = lyrics.get("albumName", "Unknown")
    matched_duration = lyrics.get("duration", 0)
    instrumental = lyrics.get("instrumental", False)

    plain_lyrics = lyrics.get("plainLyrics")
    synced_lyrics = lyrics.get("syncedLyrics")

    parsed_lyrics = parse_lrc(synced_lyrics)

    print("\n" + "=" * 68)
    print("LYRICS LOOKUP RESULT")
    print("=" * 68)

    print("Spotify metadata:")
    print(f"  Title:    {track['title']}")
    print(f"  Artist:   {track['primary_artist']}")
    print(f"  Album:    {track['album']}")
    print(
        f"  Duration: "
        f"{milliseconds_to_time(track['duration_ms'])}"
    )

    print("\nLRCLIB match:")
    print(f"  Title:    {matched_title}")
    print(f"  Artist:   {matched_artist}")
    print(f"  Album:    {matched_album}")
    print(f"  Duration: {matched_duration} seconds")
    print(f"  Instrumental: {instrumental}")

    if instrumental:
        print("\nThis track is marked as instrumental.")

    elif synced_lyrics and parsed_lyrics:
        print("\nSynchronized lyrics found.")
        print(f"Parsed timestamped lines: {len(parsed_lyrics)}")

        first_timestamp = parsed_lyrics[0]["timestamp_ms"]
        last_timestamp = parsed_lyrics[-1]["timestamp_ms"]

        print(
            "First timestamp: "
            f"{format_lrc_timestamp(first_timestamp)}"
        )

        print(
            "Last timestamp:  "
            f"{format_lrc_timestamp(last_timestamp)}"
        )

    elif plain_lyrics:
        plain_line_count = len(
            [
                line
                for line in plain_lyrics.splitlines()
                if line.strip()
            ]
        )

        print("\nOnly plain, unsynchronized lyrics were found.")
        print(f"Non-empty lines: {plain_line_count}")

    else:
        print("\nThe match does not contain usable lyrics.")

    print("=" * 68)

    return parsed_lyrics


# -------------------------------------------------------------------
# Main detection loop
# -------------------------------------------------------------------

def run_phase_two_test(spotify):
    """
    Monitor Spotify and retrieve lyrics when the track changes.
    """

    previous_track_id = None
    current_parsed_lyrics = []
    previously_inactive = False
    previous_active_lyric_index = None

    print("\nPhase 2 Spotify lyrics test is running.")
    print("Play a track in Spotify.")
    print("Press Ctrl+C to stop.\n")

    while True:
        try:
            track = get_current_track(spotify)

        except SpotifyException as error:
            if error.http_status == 429:
                retry_after = int(
                    error.headers.get(
                        "Retry-After",
                        SPOTIFY_POLL_INTERVAL,
                    )
                )

                print(
                    f"\nSpotify rate limit reached. "
                    f"Waiting {retry_after} seconds."
                )

                time.sleep(retry_after)
                continue

            print(
                f"\nSpotify API error "
                f"{error.http_status}: {error.msg}"
            )

            time.sleep(5)
            continue

        except Exception as error:
            print(f"\nSpotify connection error: {error}")
            time.sleep(5)
            continue

        if track is None:
            if not previously_inactive:
                print(
                    "\nNo active Spotify playback detected."
                )

                previously_inactive = True

            previous_track_id = None
            current_parsed_lyrics = []
            previous_active_lyric_index = None

            time.sleep(INACTIVE_POLL_INTERVAL)
            continue

        if track.get("unsupported_type"):
            if not previously_inactive:
                print(
                    "\nUnsupported Spotify item type: "
                    f"{track['unsupported_type']}"
                )

            previously_inactive = True
            time.sleep(INACTIVE_POLL_INTERVAL)
            continue

        previously_inactive = False

        if track["id"] != previous_track_id:
            print("\n" + "#" * 68)
            print("NEW TRACK DETECTED")
            print("#" * 68)
            print(f"Title:  {track['title']}")
            print(f"Artist: {track['primary_artist']}")
            print(f"Album:  {track['album']}")

            cache_data = get_lyrics_for_track(track)

            if cache_data:
                current_parsed_lyrics = print_lyrics_summary(
                    track,
                    cache_data,
                )
            else:
                current_parsed_lyrics = []
                print("No usable lyrics result for this track.")

            previous_track_id = track["id"]
            previous_active_lyric_index = None

        if current_parsed_lyrics:
            active_index = find_active_lyric_index(
                current_parsed_lyrics,
                track["progress_ms"],
            )

            # For Phase 2, we print only when the active line changes.
            if active_index != previous_active_lyric_index:
                if active_index < 0:
                    display_status = "Waiting for first lyric"
                else:
                    timestamp_ms = current_parsed_lyrics[
                        active_index
                    ]["timestamp_ms"]

                    display_status = (
                        "Lyric line changed at "
                        f"{format_lrc_timestamp(timestamp_ms)}"
                    )

                print(
                    f"\r{display_status}".ljust(120),
                    end="",
                    flush=True,
                )

                previous_active_lyric_index = active_index

        else:
            status = (
                "PLAY"
                if track["is_playing"]
                else "PAUSE"
            )

            progress = milliseconds_to_time(
                track["progress_ms"]
            )

            duration = milliseconds_to_time(
                track["duration_ms"]
            )

            print(
                f"\r{status} | {progress} / {duration} | "
                "No synchronized lyrics"
                .ljust(120),
                end="",
                flush=True,
            )

        time.sleep(SPOTIFY_POLL_INTERVAL)


def main():
    print("=" * 68)
    print("SPOTIFY LYRICS OVERLAY - PHASE 2 TEST")
    print("=" * 68)

    try:
        spotify = create_spotify_client()
        run_phase_two_test(spotify)

    except KeyboardInterrupt:
        print("\n\nPhase 2 test stopped.")

    except Exception as error:
        print("\nUnexpected startup error:")
        print(error)


if __name__ == "__main__":
    main()