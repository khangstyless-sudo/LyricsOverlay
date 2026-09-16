from pathlib import Path

from PySide6.QtCore import (
    QSettings,
    Qt,
    QTimer,
    Signal,
)
from PySide6.QtGui import (
    QAction,
    QColor,
    QFont,
    QIcon,
)
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QGraphicsDropShadowEffect,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QMenu,
    QSizeGrip,
    QSystemTrayIcon,
    QVBoxLayout,
    QWidget,
)

from lrc_parser import (
    find_active_line_index,
    get_display_lines,
    parse_lrc,
)
from lyrics_database import LyricsDatabase
from playback_clock import PlaybackClock


class LyricsOverlay(QWidget):
    quit_requested = Signal()
    offset_changed = Signal(int)

    def __init__(self):
        super().__init__()

        self.database = LyricsDatabase()
        self.playback_clock = PlaybackClock()

        self.current_spotify_track_id = None
        self.current_track_title = ""
        self.current_artist = ""

        self.stored_track = None
        self.parsed_lyrics = []
        self.previous_active_index = None

        self.locked = False
        self.dragging = False
        self.drag_start_position = None
        self.window_start_position = None

        self.settings = QSettings(
            "LocalSpotifyLyrics",
            "LyricsOverlay",
        )
        # Responsive font limits.
        self.minimum_current_font_size = 5
        self.maximum_current_font_size = 27

        self.minimum_secondary_font_size = 2
        self.maximum_secondary_font_size = 16

        self.create_window()
        self.create_interface()
        self.create_tray_icon()
        self.create_update_timer()
        self.create_resize_save_timer()
        self.restore_settings()

    def create_window(self):
        """
        Configure a frameless, translucent, always-on-top window.
        """

        self.setWindowTitle("Spotify Lyrics Overlay")

        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )

        self.setAttribute(
            Qt.WidgetAttribute.WA_TranslucentBackground,
            True,
        )

        self.setAttribute(
            Qt.WidgetAttribute.WA_ShowWithoutActivating,
            True,
        )

        self.setMinimumSize(50, 35)
        self.resize(350, 150)

    def create_interface(self):
        outer_layout = QVBoxLayout(self)

        # Extra margins prevent the text shadow from being clipped.
        outer_layout.setContentsMargins(
            8,
            8,
            8,
            8,
        )

        self.panel = QFrame()

        self.panel.setObjectName("lyricsPanel")

        panel_layout = QVBoxLayout(self.panel)

        panel_layout.setContentsMargins(
            12,
            6,
            12,
            6,
        )

        panel_layout.setSpacing(1)

        self.previous_label = QLabel("")
        self.current_label = QLabel(
            "Waiting for Spotify..."
        )
        self.next_label = QLabel("")

        for label in (
            self.previous_label,
            self.current_label,
            self.next_label,
        ):
            label.setAlignment(
                Qt.AlignmentFlag.AlignCenter
            )

            label.setWordWrap(True)

            label.setTextInteractionFlags(
                Qt.TextInteractionFlag.NoTextInteraction
            )

        self.previous_label.setObjectName(
            "previousLyric"
        )

        self.current_label.setObjectName(
            "currentLyric"
        )

        self.next_label.setObjectName(
            "nextLyric"
        )

        self.previous_label.setFont(
            QFont("Segoe UI", 7)
        )

        self.current_label.setFont(
            QFont(
                "Segoe UI",
                10,
                QFont.Weight.Bold,
            )
        )

        self.next_label.setFont(
            QFont("Segoe UI", 11)
        )

        panel_layout.addWidget(
            self.previous_label,
            stretch=1,
        )

        panel_layout.addWidget(
            self.current_label,
            stretch=2,
        )

        panel_layout.addWidget(
            self.next_label,
            stretch=1,
        )

        outer_layout.addWidget(self.panel)
                # Resize controls are only visible while the overlay is unlocked.
        resize_layout = QHBoxLayout()

        resize_layout.setContentsMargins(
            0,
            0,
            4,
            4,
        )

        resize_layout.addStretch()

        self.size_grip = QSizeGrip(self)

        self.size_grip.setFixedSize(14, 14)

        self.size_grip.setToolTip(
            "Drag to resize the lyrics overlay"
        )

        self.size_grip.setStyleSheet(
            """
            QSizeGrip {
                background-color: rgba(255, 255, 255, 45);
                border: 1px solid rgba(255, 255, 255, 75);
                border-radius: 3px;
            }

            QSizeGrip:hover {
                background-color: rgba(255, 255, 255, 130);
            }
            """
        )

        resize_layout.addWidget(self.size_grip)

        outer_layout.addLayout(resize_layout)

        self.apply_visual_style()
        self.apply_text_shadow()

    def apply_visual_style(self):
        """
        Give the labels high contrast over bright or dark content.
        """

        panel_background = (
            "rgba(8, 10, 15, 130)"
            if not self.locked
            else "rgba(8, 10, 15, 65)"
        )

        border = (
            "1px solid rgba(255, 255, 255, 80)"
            if not self.locked
            else "1px solid rgba(255, 255, 255, 0)"
        )

        self.setStyleSheet(
            f"""
            QFrame#lyricsPanel {{
                background-color: {panel_background};
                border: {border};
                border-radius: 18px;
            }}

            QLabel#previousLyric {{
                color: rgba(220, 220, 225, 150);
                background-color: transparent;
                border: none;
            }}

            QLabel#currentLyric {{
                color: rgba(255, 255, 255, 255);
                background-color: transparent;
                border: none;
            }}

            QLabel#nextLyric {{
                color: rgba(220, 220, 225, 150);
                background-color: transparent;
                border: none;
            }}
            """
        )

    def apply_text_shadow(self):
        """
        Add a shadow behind the current lyric for readability.
        """

        shadow = QGraphicsDropShadowEffect(
            self.current_label
        )

        shadow.setBlurRadius(14)
        shadow.setOffset(0, 2)
        shadow.setColor(QColor(0, 0, 0, 230))

        self.current_label.setGraphicsEffect(shadow)

    def create_update_timer(self):
        """
        Check for lyric-line changes 30 times per second.

        Text is only repainted when the selected lyric changes.
        """

        self.display_timer = QTimer(self)

        self.display_timer.timeout.connect(
            self.update_lyric_display
        )

        self.display_timer.start(33)

    def create_tray_icon(self):
        """
        Create tray controls because a locked click-through window
        cannot receive mouse input.
        """

        self.tray_icon = QSystemTrayIcon(self)

        icon = QApplication.style().standardIcon(
            QApplication.style().StandardPixmap.SP_MediaPlay
        )

        self.tray_icon.setIcon(icon)
        self.tray_icon.setToolTip(
            "Spotify Lyrics Overlay"
        )

        tray_menu = QMenu()

        self.lock_action = QAction(
            "Lock overlay and enable click-through",
            self,
        )

        self.lock_action.triggered.connect(
            self.toggle_locked_mode
        )

        self.show_action = QAction(
            "Hide overlay",
            self,
        )

        self.show_action.triggered.connect(
            self.toggle_visibility
        )

        earlier_action = QAction(
            "Show lyrics 100 ms earlier",
            self,
        )

        earlier_action.triggered.connect(
            lambda: self.adjust_offset(100)
        )

        later_action = QAction(
            "Show lyrics 100 ms later",
            self,
        )

        later_action.triggered.connect(
            lambda: self.adjust_offset(-100)
        )

        reset_offset_action = QAction(
            "Reset track offset",
            self,
        )

        reset_offset_action.triggered.connect(
            self.reset_offset
        )

        tiny_size_action = QAction(
            "Tiny: 400 x 85",
            self,
        )

        tiny_size_action.triggered.connect(
            lambda: self.resize_overlay(
                400,
                85,
            )
        )

        compact_size_action = QAction(
            "Compact: 600 x 120",
            self,
        )

        compact_size_action.triggered.connect(
            lambda: self.resize_overlay(
                600,
                120,
            )
        )

        normal_size_action = QAction(
            "Normal: 800 x 180",
            self,
        )

        normal_size_action.triggered.connect(
            lambda: self.resize_overlay(
                800,
                180,
            )
        )

        wide_size_action = QAction(
            "Wide: 1200 x 160",
            self,
        )

        wide_size_action.triggered.connect(
            lambda: self.resize_overlay(
                1200,
                160,
            )
        )

        large_size_action = QAction(
            "Large: 1000 x 250",
            self,
        )

        large_size_action.triggered.connect(
            lambda: self.resize_overlay(
                1000,
                250,
            )
        )

        custom_size_action = QAction(
            "Enter custom size...",
            self,
        )

        custom_size_action.triggered.connect(
            self.open_custom_size_dialog
        )


        fit_current_text_action = QAction(
            "Fit overlay to current lyrics",
            self,
        )

        fit_current_text_action.triggered.connect(
            self.fit_overlay_to_lyrics
        )
        
        quit_action = QAction(
            "Quit",
            self,
        )

        quit_action.triggered.connect(
            self.quit_requested.emit
        )

        tray_menu.addAction(self.lock_action)
        tray_menu.addAction(self.show_action)

        tray_menu.addSeparator()

        resize_menu = tray_menu.addMenu(
            "Resize overlay"
        )

        resize_menu.addAction(tiny_size_action)
        resize_menu.addAction(compact_size_action)
        resize_menu.addAction(normal_size_action)
        resize_menu.addAction(wide_size_action)
        resize_menu.addAction(large_size_action)

        resize_menu.addSeparator()

        resize_menu.addAction(
            fit_current_text_action
        )

        resize_menu.addAction(
            custom_size_action
        )

        tray_menu.addSeparator()

        tray_menu.addAction(earlier_action)
        tray_menu.addAction(later_action)
        tray_menu.addAction(reset_offset_action)

        tray_menu.addSeparator()

        tray_menu.addAction(quit_action)

        self.tray_icon.setContextMenu(tray_menu)

        self.tray_icon.activated.connect(
            self.on_tray_icon_activated
        )

        self.tray_icon.show()

    def on_tray_icon_activated(self, reason):
        if (
            reason
            == QSystemTrayIcon.ActivationReason.DoubleClick
        ):
            self.toggle_locked_mode()

    def handle_spotify_update(self, playback):
        """
        Receive Spotify playback information from the worker.
        """

        spotify_track_id = playback[
            "spotify_track_id"
        ]

        track_changed = (
            spotify_track_id
            != self.current_spotify_track_id
        )

        if track_changed:
            self.load_track(
                spotify_track_id=spotify_track_id,
                title=playback["title"],
                artist=playback["artist"],
            )

        self.playback_clock.synchronize(
            progress_ms=playback["progress_ms"],
            is_playing=playback["is_playing"],
            duration_ms=playback["duration_ms"],
            request_duration_ms=playback[
                "request_duration_ms"
            ],
        )

    def load_track(
        self,
        spotify_track_id,
        title,
        artist,
    ):
        """
        Search SQLite when Spotify begins a different recording.
        """

        self.current_spotify_track_id = (
            spotify_track_id
        )

        self.current_track_title = title
        self.current_artist = artist

        self.previous_active_index = None

        self.stored_track = (
            self.database.get_track_by_spotify_id(
                spotify_track_id
            )
        )

        if self.stored_track is None:
            self.parsed_lyrics = []

            self.previous_label.setText(artist)

            self.current_label.setText(
                "Lyrics not added"
            )

            self.next_label.setText(title)
            return

        self.parsed_lyrics = parse_lrc(
            self.stored_track["synced_lyrics"]
        )

        if not self.parsed_lyrics:
            self.previous_label.setText(artist)

            self.current_label.setText(
                "No synchronized timestamps"
            )

            self.next_label.setText(title)

    def update_lyric_display(self):
        """
        Select the relevant local lyric based on estimated playback.
        """

        if not self.stored_track:
            return

        if not self.parsed_lyrics:
            return

        position_ms = (
            self.playback_clock.current_position_ms()
        )

        lyrics_offset_ms = self.stored_track[
            "lyrics_offset_ms"
        ]

        adjusted_position_ms = (
            position_ms
            + lyrics_offset_ms
        )

        active_index = find_active_line_index(
            self.parsed_lyrics,
            adjusted_position_ms,
        )

        if active_index == self.previous_active_index:
            return

        display_lines = get_display_lines(
            self.parsed_lyrics,
            active_index,
        )

        self.previous_label.setText(
            display_lines["previous"]
        )

        current_text = display_lines["current"]

        if not current_text and active_index < 0:
            current_text = self.current_track_title

        self.current_label.setText(current_text)

        # First calculate the normal responsive size.
        self.update_responsive_font_sizes()

        # Then apply a small reduction for a very long lyric line.
        fitted_font_size = (
            self.calculate_current_line_font_size(
                current_text
            )
        )

        current_font = self.current_label.font()
        current_font.setPointSize(fitted_font_size)
        self.current_label.setFont(current_font)

        self.next_label.setText(
            display_lines["next"]
        )

        self.previous_active_index = active_index

    def handle_inactive_playback(self):
        self.playback_clock.reset()

        self.current_spotify_track_id = None
        self.stored_track = None
        self.parsed_lyrics = []
        self.previous_active_index = None

        self.previous_label.setText("")
        self.current_label.setText(
            "Spotify is not playing"
        )
        self.next_label.setText("")

    def handle_connection_error(self, message):
        """
        Keep existing lyrics visible where possible, but expose the
        connection error through the tray tooltip.
        """

        self.tray_icon.setToolTip(
            f"Spotify Lyrics Overlay\n{message}"
        )

    def toggle_locked_mode(self):
        self.set_locked_mode(not self.locked)

    def set_locked_mode(self, locked):
        """
        Locked:
            Mouse input passes through to applications behind it.

        Unlocked:
            The overlay can be moved by dragging it.
        """

        self.locked = locked

        flags = (
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )

        if locked:
            flags |= (
                Qt.WindowType.WindowTransparentForInput
            )

        current_position = self.pos()
        current_size = self.size()

        self.setWindowFlags(flags)

        self.move(current_position)
        self.resize(current_size)

        self.apply_visual_style()

        # Do not show resizing controls in locked click-through mode.
        self.size_grip.setVisible(
            not locked
        )

        self.show()

        if locked:
            self.lock_action.setText(
                "Unlock overlay for moving"
            )

            self.tray_icon.showMessage(
                "Spotify Lyrics Overlay",
                "Overlay locked. Mouse clicks now "
                "pass through it.",
                QSystemTrayIcon.MessageIcon.Information,
                2000,
            )
        else:
            self.lock_action.setText(
                "Lock overlay and enable click-through"
            )

            self.tray_icon.showMessage(
                "Spotify Lyrics Overlay",
                "Edit mode enabled. Drag the overlay "
                "to move it.",
                QSystemTrayIcon.MessageIcon.Information,
                2000,
            )

        self.save_settings()

    def toggle_visibility(self):
        if self.isVisible():
            self.hide()
            self.show_action.setText(
                "Show overlay"
            )
        else:
            self.show()
            self.raise_()
            self.show_action.setText(
                "Hide overlay"
            )

    def resize_overlay(
        self,
        width,
        height,
    ):
        """
        Resize the overlay to a predefined or manually entered size.
        """

        width = max(
            self.minimumWidth(),
            int(width),
        )

        height = max(
            self.minimumHeight(),
            int(height),
        )

        self.resize(width, height)

        self.keep_overlay_on_screen()
        self.save_settings()

        self.tray_icon.showMessage(
            "Overlay resized",
            f"New size: {width} x {height}",
            QSystemTrayIcon.MessageIcon.Information,
            1500,
        )

    def open_custom_size_dialog(self):
        """
        Ask for an exact overlay width and height.
        """

        width, width_accepted = (
            QInputDialog.getInt(
                self,
                "Custom overlay width",
                "Width in pixels:",
                self.width(),
                280,
                3840,
                20,
            )
        )

        if not width_accepted:
            return

        height, height_accepted = (
            QInputDialog.getInt(
                self,
                "Custom overlay height",
                "Height in pixels:",
                self.height(),
                75,
                2160,
                5,
            )
        )

        if not height_accepted:
            return

        self.resize_overlay(
            width,
            height,
        )

    def fit_overlay_to_lyrics(self):
        """
        Resize the overlay based on the current label size hints.

        Width remains unchanged because lyric wrapping depends on it.
        Height is adjusted to show the three lyric labels comfortably.
        """

        self.previous_label.adjustSize()
        self.current_label.adjustSize()
        self.next_label.adjustSize()

        content_height = (
            self.previous_label.sizeHint().height()
            + self.current_label.sizeHint().height()
            + self.next_label.sizeHint().height()
            + 110
        )

        content_height = max(
            self.minimumHeight(),
            content_height,
        )

        content_height = min(
            700,
            content_height,
        )

        self.resize_overlay(
            self.width(),
            content_height,
        )

    def keep_overlay_on_screen(self):
        """
        Keep the overlay inside the screen after resizing.

        This prevents part of a large overlay from becoming inaccessible.
        """

        screen = QApplication.screenAt(
            self.frameGeometry().center()
        )

        if screen is None:
            screen = QApplication.primaryScreen()

        if screen is None:
            return

        available_area = screen.availableGeometry()

        new_x = self.x()
        new_y = self.y()

        if self.width() > available_area.width():
            self.resize(
                available_area.width(),
                self.height(),
            )

        if self.height() > available_area.height():
            self.resize(
                self.width(),
                available_area.height(),
            )

        maximum_x = (
            available_area.right()
            - self.width()
            + 1
        )

        maximum_y = (
            available_area.bottom()
            - self.height()
            + 1
        )

        new_x = max(
            available_area.left(),
            min(new_x, maximum_x),
        )

        new_y = max(
            available_area.top(),
            min(new_y, maximum_y),
        )

        self.move(
            new_x,
            new_y,
        )

    def adjust_offset(self, adjustment_ms):
        """
        Positive values make lyrics appear earlier.
        Negative values make lyrics appear later.
        """

        if not self.stored_track:
            self.tray_icon.showMessage(
                "Lyrics offset",
                "The current song is not in the "
                "local lyrics database.",
                QSystemTrayIcon.MessageIcon.Warning,
                2000,
            )
            return

        new_offset = (
            self.stored_track["lyrics_offset_ms"]
            + adjustment_ms
        )

        updated = (
            self.database.update_lyrics_offset(
                self.current_spotify_track_id,
                new_offset,
            )
        )

        if not updated:
            return

        self.stored_track["lyrics_offset_ms"] = (
            new_offset
        )

        self.previous_active_index = None

        direction = (
            "earlier"
            if adjustment_ms > 0
            else "later"
        )

        self.tray_icon.showMessage(
            "Lyrics offset updated",
            f"Lyrics will appear {direction}.\n"
            f"Current offset: {new_offset} ms",
            QSystemTrayIcon.MessageIcon.Information,
            1600,
        )

        self.offset_changed.emit(new_offset)

    def reset_offset(self):
        if not self.stored_track:
            return

        updated = (
            self.database.update_lyrics_offset(
                self.current_spotify_track_id,
                0,
            )
        )

        if updated:
            self.stored_track["lyrics_offset_ms"] = 0
            self.previous_active_index = None

            self.tray_icon.showMessage(
                "Lyrics offset",
                "Track offset reset to 0 ms.",
                QSystemTrayIcon.MessageIcon.Information,
                1600,
            )

    def mousePressEvent(self, event):
        if self.locked:
            return

        if (
            event.button()
            == Qt.MouseButton.LeftButton
        ):
            self.dragging = True

            self.drag_start_position = (
                event.globalPosition().toPoint()
            )

            self.window_start_position = self.pos()

            event.accept()

    def mouseMoveEvent(self, event):
        if not self.dragging or self.locked:
            return

        current_position = (
            event.globalPosition().toPoint()
        )

        movement = (
            current_position
            - self.drag_start_position
        )

        self.move(
            self.window_start_position
            + movement
        )

        event.accept()

    def mouseReleaseEvent(self, event):
        if (
            event.button()
            == Qt.MouseButton.LeftButton
        ):
            self.dragging = False
            self.save_settings()
            event.accept()

    def restore_settings(self):
        saved_x = self.settings.value(
            "overlay_x",
            None,
        )

        saved_y = self.settings.value(
            "overlay_y",
            None,
        )

        saved_width = int(
            self.settings.value(
                "overlay_width",
                1000,
            )
        )

        saved_height = int(
            self.settings.value(
                "overlay_height",
                230,
            )
        )

        self.resize(
            saved_width,
            saved_height,
        )

        if saved_x is not None and saved_y is not None:
            self.move(
                int(saved_x),
                int(saved_y),
            )
        else:
            self.move_to_default_position()

        saved_locked = (
            self.settings.value(
                "overlay_locked",
                False,
                type=bool,
            )
        )

        if saved_locked:
            QTimer.singleShot(
                100,
                lambda: self.set_locked_mode(True),
            )

    def move_to_default_position(self):
        screen = QApplication.primaryScreen()

        if not screen:
            self.move(200, 700)
            return

        available_area = (
            screen.availableGeometry()
        )

        x = (
            available_area.x()
            + (
                available_area.width()
                - self.width()
            )
            // 2
        )

        y = (
            available_area.y()
            + available_area.height()
            - self.height()
            - 60
        )

        self.move(x, y)

    def save_settings(self):
        self.settings.setValue(
            "overlay_x",
            self.x(),
        )

        self.settings.setValue(
            "overlay_y",
            self.y(),
        )

        self.settings.setValue(
            "overlay_width",
            self.width(),
        )

        self.settings.setValue(
            "overlay_height",
            self.height(),
        )

        self.settings.setValue(
            "overlay_locked",
            self.locked,
        )

        self.settings.sync()

    def resizeEvent(self, event):
        """
        Scale fonts and simplify the displayed content according to
        the available overlay size.
        """

        super().resizeEvent(event)

        self.update_responsive_font_sizes()

        height = self.height()

        # Very compact mode:
        # Show only the current lyric.
        if height < 120:
            self.previous_label.setVisible(False)
            self.next_label.setVisible(False)

        # Medium mode:
        # Show the current and next lyric.
        elif height < 175:
            self.previous_label.setVisible(False)
            self.next_label.setVisible(True)

        # Full mode:
        # Show previous, current and next lyrics.
        else:
            self.previous_label.setVisible(True)
            self.next_label.setVisible(True)

        if hasattr(self, "resize_save_timer"):
            self.resize_save_timer.start()

    def create_resize_save_timer(self):
        """
        Delay saving until the user has stopped resizing.
        """

        self.resize_save_timer = QTimer(self)

        self.resize_save_timer.setSingleShot(True)

        self.resize_save_timer.setInterval(400)

        self.resize_save_timer.timeout.connect(
            self.save_settings
        )

    def update_responsive_font_sizes(self):
        """
        Scale lyric fonts based on the current overlay dimensions.

        Both width and height affect the resulting font size.
        The smaller dimension becomes the limiting factor.
        """

        width = max(1, self.width())
        height = max(1, self.height())

        # 1000 x 230 was the original design size.
        width_scale = width / 1000
        height_scale = height / 230

        # Use the more restrictive dimension so text does not overflow.
        scale = min(width_scale, height_scale)

        current_font_size = round(
            self.maximum_current_font_size * scale
        )

        secondary_font_size = round(
            self.maximum_secondary_font_size * scale
        )

        current_font_size = max(
            self.minimum_current_font_size,
            min(
                current_font_size,
                self.maximum_current_font_size,
            ),
        )

        secondary_font_size = max(
            self.minimum_secondary_font_size,
            min(
                secondary_font_size,
                self.maximum_secondary_font_size,
            ),
        )

        current_font = QFont(
            "Segoe UI",
            current_font_size,
            QFont.Weight.Bold,
        )

        secondary_font = QFont(
            "Segoe UI",
            secondary_font_size,
            QFont.Weight.Normal,
        )

        self.current_label.setFont(current_font)
        self.previous_label.setFont(secondary_font)
        self.next_label.setFont(secondary_font)

    def calculate_current_line_font_size(self, text):
        """
        Apply a small additional reduction for unusually long lines.
        """

        base_font_size = self.current_label.font().pointSize()

        text_length = len(text.strip())

        if text_length > 100:
            return max(
                self.minimum_current_font_size,
                base_font_size - 4,
            )

        if text_length > 75:
            return max(
                self.minimum_current_font_size,
                base_font_size - 3,
            )

        if text_length > 50:
            return max(
                self.minimum_current_font_size,
                base_font_size - 1,
            )

        return base_font_size
    
    def closeEvent(self, event):
        """
        Closing the frameless window hides it instead of ending the app.
        Use the tray-menu Quit command to terminate everything.
        """

        self.save_settings()
        event.ignore()
        self.hide()
        self.show_action.setText("Show overlay")