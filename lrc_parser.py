import re
from bisect import bisect_right


LRC_TIMESTAMP_PATTERN = re.compile(
    r"\[(?P<minutes>\d{1,3}):"
    r"(?P<seconds>\d{2})"
    r"(?:\.(?P<fraction>\d{1,3}))?\]"
)


def fraction_to_milliseconds(fraction):
    if not fraction:
        return 0

    return int(
        fraction.ljust(3, "0")[:3]
    )


def parse_lrc(synced_lyrics):
    """
    Convert LRC text into:

    [
        {
            "timestamp_ms": 5000,
            "text": "First line"
        }
    ]
    """

    parsed_lines = []

    if not synced_lyrics:
        return parsed_lines

    for raw_line in synced_lyrics.splitlines():
        matches = list(
            LRC_TIMESTAMP_PATTERN.finditer(raw_line)
        )

        if not matches:
            continue

        lyric_text = LRC_TIMESTAMP_PATTERN.sub(
            "",
            raw_line,
        ).strip()

        for match in matches:
            minutes = int(match.group("minutes"))
            seconds = int(match.group("seconds"))

            milliseconds = fraction_to_milliseconds(
                match.group("fraction")
            )

            timestamp_ms = (
                minutes * 60_000
                + seconds * 1_000
                + milliseconds
            )

            parsed_lines.append(
                {
                    "timestamp_ms": timestamp_ms,
                    "text": lyric_text,
                }
            )

    parsed_lines.sort(
        key=lambda item: item["timestamp_ms"]
    )

    return parsed_lines


def find_active_line_index(
    parsed_lyrics,
    playback_position_ms,
):
    if not parsed_lyrics:
        return -1

    timestamps = [
        line["timestamp_ms"]
        for line in parsed_lyrics
    ]

    return (
        bisect_right(
            timestamps,
            playback_position_ms,
        )
        - 1
    )


def get_display_lines(
    parsed_lyrics,
    active_index,
):
    """
    Return previous, current and next lyric lines.
    """

    if active_index < 0:
        next_line = (
            parsed_lyrics[0]["text"]
            if parsed_lyrics
            else ""
        )

        return {
            "previous": "",
            "current": "",
            "next": next_line,
        }

    previous_text = ""

    if active_index > 0:
        previous_text = parsed_lyrics[
            active_index - 1
        ]["text"]

    current_text = parsed_lyrics[
        active_index
    ]["text"]

    next_text = ""

    if active_index + 1 < len(parsed_lyrics):
        next_text = parsed_lyrics[
            active_index + 1
        ]["text"]

    return {
        "previous": previous_text,
        "current": current_text,
        "next": next_text,
    }