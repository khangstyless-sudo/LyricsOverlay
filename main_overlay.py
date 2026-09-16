import sys

from PySide6.QtCore import QThread
from PySide6.QtWidgets import (
    QApplication,
    QMessageBox,
    QSystemTrayIcon,
)

from overlay import LyricsOverlay
from spotify_worker import SpotifyWorker


class LyricsApplication:
    def __init__(self):
        self.app = QApplication(sys.argv)

        # The application must continue running when the overlay
        # is hidden because the tray icon remains active.
        self.app.setQuitOnLastWindowClosed(False)

        self.overlay = LyricsOverlay()

        self.spotify_thread = QThread()
        self.spotify_worker = SpotifyWorker(
            poll_interval_seconds=2.0,
            inactive_interval_seconds=4.0,
        )

        self.spotify_worker.moveToThread(
            self.spotify_thread
        )

        self.spotify_thread.started.connect(
            self.spotify_worker.run
        )

        self.spotify_worker.playback_updated.connect(
            self.overlay.handle_spotify_update
        )

        self.spotify_worker.playback_inactive.connect(
            self.overlay.handle_inactive_playback
        )

        self.spotify_worker.connection_error.connect(
            self.overlay.handle_connection_error
        )

        self.spotify_worker.finished.connect(
            self.spotify_thread.quit
        )

        self.overlay.quit_requested.connect(
            self.quit_application
        )

        self.app.aboutToQuit.connect(
            self.stop_worker
        )

    def run(self):
        if not QSystemTrayIcon.isSystemTrayAvailable():
            QMessageBox.warning(
                None,
                "System tray unavailable",
                "The system tray is unavailable. "
                "The overlay will still run, but locked "
                "mode may be difficult to control.",
            )

        self.overlay.show()
        self.overlay.raise_()

        self.spotify_thread.start()

        return self.app.exec()

    def stop_worker(self):
        if self.spotify_worker:
            self.spotify_worker.stop()

        if self.spotify_thread.isRunning():
            self.spotify_thread.quit()

            if not self.spotify_thread.wait(3000):
                self.spotify_thread.terminate()
                self.spotify_thread.wait()

    def quit_application(self):
        self.overlay.save_settings()
        self.stop_worker()

        if self.overlay.tray_icon:
            self.overlay.tray_icon.hide()

        self.app.quit()


def main():
    application = LyricsApplication()
    exit_code = application.run()
    sys.exit(exit_code)


if __name__ == "__main__":
    main()