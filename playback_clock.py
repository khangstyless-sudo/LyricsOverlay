import time


class PlaybackClock:
    """
    Estimate the current playback position between Spotify API updates.

    Spotify periodically provides an authoritative progress_ms value.
    Between those responses, this class advances the position using
    Python's monotonic clock.
    """

    def __init__(self):
        self.base_position_ms = 0.0
        self.synchronized_at = time.monotonic()
        self.is_playing = False
        self.duration_ms = 0

    def synchronize(
        self,
        progress_ms,
        is_playing,
        duration_ms=0,
        request_duration_ms=0,
    ):
        """
        Synchronize the local clock with Spotify.

        Half of the API request duration is added as a basic network
        latency estimate.
        """

        progress_ms = progress_ms or 0
        duration_ms = duration_ms or 0
        request_duration_ms = request_duration_ms or 0

        network_adjustment_ms = 0

        if is_playing:
            network_adjustment_ms = request_duration_ms / 2

        self.base_position_ms = (
            float(progress_ms)
            + network_adjustment_ms
        )

        self.synchronized_at = time.monotonic()
        self.is_playing = bool(is_playing)
        self.duration_ms = int(duration_ms)

    def current_position_ms(self):
        """
        Return the estimated current playback position.
        """

        position_ms = self.base_position_ms

        if self.is_playing:
            elapsed_ms = (
                time.monotonic()
                - self.synchronized_at
            ) * 1000

            position_ms += elapsed_ms

        if self.duration_ms > 0:
            position_ms = min(
                position_ms,
                self.duration_ms,
            )

        return max(0, int(position_ms))

    def reset(self):
        self.base_position_ms = 0.0
        self.synchronized_at = time.monotonic()
        self.is_playing = False
        self.duration_ms = 0