# =========================================================
# PRODUCTION-SAFE DROP-IN OVERLAY UI
# =========================================================

import sys
import urllib.request
import webbrowser
import markdown

from PyQt6.QtWidgets import (
    QApplication,
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QTextBrowser,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QFrame,
    QSizePolicy,
    QGraphicsDropShadowEffect,
)

from PyQt6.QtCore import (
    Qt,
    QPoint,
    QTimer,
    pyqtSignal,
    QThread,
    QSize,
)

from PyQt6.QtGui import (
    QFont,
    QColor,
    QPixmap,
    QImage,
)

import ctypes
from ctypes import wintypes
# =========================================================
# IMAGE LOADER
# =========================================================

class ImageLoaderWorker(QThread):

    image_loaded = pyqtSignal(str, QPixmap)

    def __init__(self, media_url):
        super().__init__()

        self.media_url = media_url
        self.running = True

    def run(self):

        if not self.running:
            return

        try:

            req = urllib.request.Request(
                self.media_url,
                headers={
                    "User-Agent": "Mozilla/5.0"
                }
            )

            with urllib.request.urlopen(
                req,
                timeout=8
            ) as response:

                if not self.running:
                    return

                data = response.read()

            if not self.running:
                return

            image = QImage()

            if image.loadFromData(data):

                pixmap = QPixmap.fromImage(image)

                if (
                    not pixmap.isNull()
                    and self.running
                ):
                    self.image_loaded.emit(
                        self.media_url,
                        pixmap
                    )

        except Exception as e:
            print("Image load error:", e)

    def stop(self):
        """
        Signal the worker to stop and disconnect
        its signal so late emissions cannot reach
        widgets that may have already been deleted.
        """
        self.running = False
        try:
            self.image_loaded.disconnect()
        except Exception:
            pass


# =========================================================
# TOPIC CARD
# =========================================================

class TopicCard(QFrame):

    def __init__(
        self,
        title,
        content,
        url=None,
        media=None,
        card_type="info",
        parent=None
    ):
        super().__init__(parent)

        self.expanded = False
        self.worker = None

        border_color = {
            "info":    "#3B82F6",
            "success": "#10B981",
            "warning": "#F59E0B",
            "error":   "#EF4444",
        }.get(card_type, "#3B82F6")

        self.setObjectName("TopicCard")

        self.setStyleSheet(f"""
            QFrame#TopicCard {{
                background-color: #FFFFFF;
                border-radius: 14px;
                border: 1px solid #E5E7EB;
                border-left: 4px solid {border_color};
            }}
        """)

        # -------------------------------------------------
        # SHADOW
        # -------------------------------------------------

        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(10)
        shadow.setOffset(0, 2)
        shadow.setColor(QColor(0, 0, 0, 18))
        self.setGraphicsEffect(shadow)

        # -------------------------------------------------
        # MAIN LAYOUT
        # -------------------------------------------------

        self.main_layout = QVBoxLayout(self)
        self.main_layout.setContentsMargins(12, 12, 12, 12)
        self.main_layout.setSpacing(8)

        # -------------------------------------------------
        # HEADER
        # -------------------------------------------------

        header = QHBoxLayout()

        self.title_label = QLabel(title)
        self.title_label.setFont(
            QFont("Segoe UI", 11, QFont.Weight.Bold)
        )
        self.title_label.setStyleSheet("""
            color: #111827;
            border: none;
            background: transparent;
        """)

        header.addWidget(self.title_label)
        header.addStretch()

        self.expand_btn = QPushButton("▾")
        self.expand_btn.setFixedSize(24, 24)
        self.expand_btn.setCursor(
            Qt.CursorShape.PointingHandCursor
        )
        self.expand_btn.setStyleSheet("""
            QPushButton {
                border: none;
                background: transparent;
                color: #6B7280;
                font-size: 14px;
                font-weight: bold;
            }
            QPushButton:hover {
                color: #111827;
            }
        """)
        self.expand_btn.clicked.connect(self.toggle_expand)
        header.addWidget(self.expand_btn)

        self.main_layout.addLayout(header)

        # -------------------------------------------------
        # CONTENT
        # -------------------------------------------------

        self.content_browser = QTextBrowser()
        self.content_browser.setOpenExternalLinks(True)
        self.content_browser.setStyleSheet("""
            QTextBrowser {
                border: none;
                background: transparent;
                color: #374151;
                font-size: 13px;
                font-family: 'Segoe UI';
                padding: 0px;
            }
        """)
        self.content_browser.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Minimum
        )

        html = markdown.markdown(
            content,
            extensions=["extra", "nl2br"]
        )
        self.content_browser.setHtml(html)

        self.content_browser.setVerticalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self.content_browser.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self.content_browser.document().setTextWidth(500)

        self.collapsed_height = self.calculate_height(content)
        self.content_browser.setFixedHeight(self.collapsed_height)

        self.main_layout.addWidget(self.content_browser)

        # -------------------------------------------------
        # IMAGE
        # -------------------------------------------------

        self.media_label = None

        if media:

            self.media_label = QLabel("Loading image...")
            self.media_label.setAlignment(
                Qt.AlignmentFlag.AlignCenter
            )
            self.media_label.setFixedHeight(180)
            self.media_label.setStyleSheet("""
                QLabel {
                    border-radius: 10px;
                    background-color: #F3F4F6;
                    color: #9CA3AF;
                    padding: 10px;
                }
            """)
            self.main_layout.addWidget(self.media_label)

            self.worker = ImageLoaderWorker(media)
            self.worker.image_loaded.connect(
                self.on_image_loaded,
                Qt.ConnectionType.QueuedConnection
            )
            self.worker.finished.connect(
                self.worker.deleteLater
            )
            self.worker.start()

        # -------------------------------------------------
        # SOURCE BUTTON
        # -------------------------------------------------

        if url:

            footer = QHBoxLayout()

            self.link_btn = QPushButton("🔗 Source")
            self.link_btn.setCursor(
                Qt.CursorShape.PointingHandCursor
            )
            self.link_btn.setFixedHeight(28)
            self.link_btn.setStyleSheet("""
                QPushButton {
                    background-color: #EFF6FF;
                    border: 1px solid #BFDBFE;
                    border-radius: 8px;
                    color: #2563EB;
                    padding: 0 12px;
                    font-size: 11px;
                    font-weight: 600;
                }
                QPushButton:hover {
                    background-color: #DBEAFE;
                }
            """)
            self.link_btn.clicked.connect(
                lambda: webbrowser.open(url)
            )

            footer.addWidget(self.link_btn)
            footer.addStretch()
            self.main_layout.addLayout(footer)

    # -------------------------------------------------
    # CLEANUP
    # Stop the worker thread and disconnect its signal
    # BEFORE any widget teardown so that late emissions
    # from the background thread cannot reach a widget
    # that has already been deleted (the root cause of
    # QObject::setParent cross-thread crashes).
    # -------------------------------------------------

    def cleanup(self):
        if self.worker:
            try:
                self.worker.stop()               # sets running=False, disconnects signal
                if not self.worker.isFinished():
                    self.worker.quit()
                    self.worker.wait(500)        # short wait; app may be closing
            except Exception:
                pass
            self.worker = None

    # -------------------------------------------------
    # HEIGHT
    # -------------------------------------------------

    def calculate_height(self, text):
        chars = len(text)
        estimated = 70 + min(chars // 4, 200)
        return min(max(estimated, 100), 250)

    # -------------------------------------------------
    # IMAGE LOADED
    # -------------------------------------------------

    def on_image_loaded(self, media_url, pixmap):
        if not self.media_label:
            return

        scaled = pixmap.scaled(
            QSize(520, 180),
            Qt.AspectRatioMode.KeepAspectRatioByExpanding,
            Qt.TransformationMode.SmoothTransformation
        )
        self.media_label.setPixmap(scaled)
        self.media_label.setStyleSheet("""
            border-radius: 10px;
            background: transparent;
        """)

    # -------------------------------------------------
    # EXPAND / COLLAPSE
    # -------------------------------------------------

    def toggle_expand(self):
        self.expanded = not self.expanded

        if self.expanded:
            doc_height = int(
                self.content_browser.document().size().height()
            ) + 20
            self.content_browser.setFixedHeight(
                min(doc_height, 1200)
            )
            self.expand_btn.setText("▴")
        else:
            self.content_browser.setFixedHeight(
                self.collapsed_height
            )
            self.expand_btn.setText("▾")


# =========================================================
# OVERLAY WINDOW
# =========================================================

class OverlayWindow(QWidget):

    update_signal         = pyqtSignal(str, str)
    populate_cards_signal = pyqtSignal(list)
    append_chat_signal    = pyqtSignal(str, str)
    move_signal           = pyqtSignal(int, int)
    user_input_signal     = pyqtSignal(str)
    cancel_signal         = pyqtSignal()
    shutdown_signal       = pyqtSignal()

    def __init__(self):
        super().__init__()

        self.drag_pos    = QPoint()
        self.topic_cards = []

        self.init_ui()

        self.update_signal.connect(self.update_display)
        self.populate_cards_signal.connect(self.populate_cards)
        self.append_chat_signal.connect(self.append_chat_message)
        self.move_signal.connect(self._move_to_cursor)

    # -------------------------------------------------
    # UI
    # -------------------------------------------------

    def init_ui(self):

        self.enable_capture_protection()

        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint |
            Qt.WindowType.WindowStaysOnTopHint |
            Qt.WindowType.Tool |
            Qt.WindowType.BypassWindowManagerHint
        )
        self.setAttribute(
            Qt.WidgetAttribute.WA_NoSystemBackground, False
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)

        self.resize(620, 650)
        self.setMinimumSize(520, 320)
        self.setMaximumSize(900, 900)

        root = QVBoxLayout(self)
        root.setContentsMargins(10, 10, 10, 10)

        # PANEL
        self.panel = QFrame()
        self.panel.setStyleSheet("""
            QFrame {
                background-color: #F8FAFC;
                border-radius: 18px;
                border: 1px solid #E5E7EB;
            }
        """)

        shadow = QGraphicsDropShadowEffect(self.panel)
        shadow.setBlurRadius(12)
        shadow.setOffset(0, 2)
        shadow.setColor(QColor(0, 0, 0, 30))
        self.panel.setGraphicsEffect(shadow)

        panel_layout = QVBoxLayout(self.panel)
        panel_layout.setContentsMargins(14, 14, 14, 14)
        panel_layout.setSpacing(10)

        # HEADER
        header = QHBoxLayout()

        self.status_label = QLabel("Ready")
        self.status_label.setStyleSheet("""
            color: white;
            background-color: #10B981;
            padding: 5px 12px;
            border-radius: 8px;
            font-size: 12px;
            font-weight: 700;
        """)
        header.addWidget(self.status_label)
        header.addStretch()

        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.setFixedHeight(28)
        self.cancel_btn.clicked.connect(self.on_cancel)
        self.cancel_btn.setStyleSheet("""
            QPushButton {
                background-color: #FEF2F2;
                color: #DC2626;
                border-radius: 8px;
                border: 1px solid #FECACA;
                padding: 0 12px;
                font-size: 11px;
                font-weight: 600;
            }
            QPushButton:hover {
                background-color: #FEE2E2;
            }
        """)
        self.cancel_btn.hide()
        header.addWidget(self.cancel_btn)

        self.shutdown_btn = QPushButton("Quit")
        self.shutdown_btn.setFixedHeight(28)
        self.shutdown_btn.clicked.connect(self.on_shutdown)
        self.shutdown_btn.setStyleSheet("""
            QPushButton {
                background-color: #EF4444;
                color: white;
                border-radius: 8px;
                border: none;
                padding: 0 12px;
                font-size: 11px;
                font-weight: 600;
            }
            QPushButton:hover {
                background-color: #DC2626;
            }
        """)
        header.addWidget(self.shutdown_btn)

        self.close_btn = QPushButton("✕")
        self.close_btn.setFixedSize(28, 28)
        self.close_btn.clicked.connect(self.hide)
        self.close_btn.setStyleSheet("""
            QPushButton {
                background: transparent;
                border: none;
                color: #6B7280;
                font-size: 15px;
                font-weight: bold;
            }
            QPushButton:hover {
                color: #EF4444;
                background-color: #FEE2E2;
                border-radius: 14px;
            }
        """)
        header.addWidget(self.close_btn)

        panel_layout.addLayout(header)

        # SCROLL AREA
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setStyleSheet("""
            QScrollArea {
                border: none;
                background: transparent;
            }
            QScrollBar:vertical {
                width: 8px;
                background: transparent;
            }
            QScrollBar::handle:vertical {
                background: rgba(156,163,175,120);
                border-radius: 4px;
            }
        """)

        self.scroll_widget = QWidget()
        self.scroll_layout = QVBoxLayout(self.scroll_widget)
        self.scroll_layout.setSpacing(12)
        self.scroll_layout.setContentsMargins(0, 0, 0, 0)
        self.scroll.setWidget(self.scroll_widget)

        panel_layout.addWidget(self.scroll)

        # INPUT
        self.input_field = QLineEdit()
        self.input_field.setPlaceholderText("Ask follow-up...")
        self.input_field.setFixedHeight(42)
        self.input_field.returnPressed.connect(self.on_input_entered)
        self.input_field.setStyleSheet("""
            QLineEdit {
                background-color: white;
                border-radius: 10px;
                border: 1px solid #D1D5DB;
                padding: 0 14px;
                color: #111827;
                font-size: 14px;
            }
            QLineEdit:focus {
                border: 1px solid #3B82F6;
            }
        """)
        panel_layout.addWidget(self.input_field)

        root.addWidget(self.panel)
        self.hide()

    def enable_capture_protection(self):
        """
        Prevent the window from appearing in:
        - screenshots
        - screen recordings
        - OBS display capture
        - Windows capture APIs

        Windows 10 2004+ only for full support.
        """

        try:
            hwnd = int(self.winId())

            user32 = ctypes.windll.user32

            # Windows 10 2004+
            WDA_EXCLUDEFROMCAPTURE = 0x11

            result = user32.SetWindowDisplayAffinity(
                wintypes.HWND(hwnd),
                wintypes.DWORD(WDA_EXCLUDEFROMCAPTURE)
            )

            if result == 0:
                print("Failed to enable capture protection")

        except Exception as e:
            print("Capture protection error:", e)
    # -------------------------------------------------
    # CLEANUP HELPERS
    # Call _cleanup_all_cards() early (before any Qt
    # teardown) to guarantee worker threads are stopped
    # while the event loop is still fully alive.  This
    # prevents both the "Cannot set parent" cross-thread
    # warning and the "event dispatcher already destroyed"
    # QBasicTimer crash.
    # -------------------------------------------------

    def _cleanup_all_cards(self):
        for card in self.topic_cards:
            try:
                card.cleanup()
            except Exception:
                pass
        self.topic_cards.clear()

    def closeEvent(self, event):
        self._cleanup_all_cards()
        event.accept()

    # -------------------------------------------------
    # PUBLIC API  (call from any thread)
    # -------------------------------------------------

    def update_state(self, state, text=""):
        self.update_signal.emit(state, text)

    def populate_cards_external(self, cards):
        """Thread-safe: always use this, never call populate_cards() directly."""
        self.populate_cards_signal.emit(cards)

    def append_chat(self, sender, text):
        self.append_chat_signal.emit(sender, text)

    def move_to_cursor(self, x, y):
        self.move_signal.emit(int(x), int(y))

    # -------------------------------------------------
    # MOVE  (main thread only — called via move_signal)
    # -------------------------------------------------

    def _move_to_cursor(self, x, y):
        screen   = QApplication.primaryScreen()
        target_x = x + 20
        target_y = y + 20

        if screen:
            geom = screen.availableGeometry()
            target_x = max(
                geom.left() + 10,
                min(target_x, geom.right() - self.width() - 10)
            )
            target_y = max(
                geom.top() + 10,
                min(target_y, geom.bottom() - self.height() - 10)
            )

        self.move(target_x, target_y)
        self.show()

    # -------------------------------------------------
    # POPULATE CARDS  (main thread only — called via signal)
    # -------------------------------------------------

    def populate_cards(self, cards):

        # Stop workers first, THEN schedule widget deletion.
        for card in self.topic_cards:
            try:
                card.cleanup()
            except Exception:
                pass
            card.deleteLater()

        self.topic_cards.clear()

        # Drain the scroll layout
        while self.scroll_layout.count():
            item   = self.scroll_layout.takeAt(0)
            widget = item.widget()
            if widget:
                widget.deleteLater()

        for c in cards:
            card = TopicCard(
                title     = c.get("title",   "Response"),
                content   = c.get("content", ""),
                url       = c.get("url"),
                media     = c.get("media"),
                card_type = c.get("type",    "info"),
            )
            self.scroll_layout.addWidget(card)
            self.topic_cards.append(card)

        self.scroll_layout.addStretch()

        QTimer.singleShot(
            50,
            lambda: self.scroll.verticalScrollBar().setValue(0)
        )

    # -------------------------------------------------
    # CHAT  (main thread only — called via signal)
    # -------------------------------------------------

    def append_chat_message(self, sender, text):
        print(f"{sender}: {text}")

    # -------------------------------------------------
    # STATUS
    # -------------------------------------------------

    def update_display(self, state, text=""):

        color_map = {
            "ready":     "#10B981",
            "thinking":  "#F59E0B",
            "error":     "#EF4444",
            "chat":      "#8B5CF6",
            "listening": "#2563EB",
        }
        color = color_map.get(state, "#10B981")

        self.status_label.setText(state.capitalize())
        self.status_label.setStyleSheet(f"""
            color: white;
            background-color: {color};
            padding: 5px 12px;
            border-radius: 8px;
            font-size: 12px;
            font-weight: 700;
        """)

        if state == "ready":
            self.cancel_btn.hide()
        else:
            self.cancel_btn.show()

        self.show()

    # -------------------------------------------------
    # INPUT
    # -------------------------------------------------

    def on_input_entered(self):
        text = self.input_field.text().strip()
        if not text:
            return
        self.input_field.clear()
        self.update_display("thinking")
        self.user_input_signal.emit(text)

    # -------------------------------------------------
    # CANCEL
    # -------------------------------------------------

    def on_cancel(self):
        self.cancel_signal.emit()

    # -------------------------------------------------
    # SHUTDOWN
    # Clean up worker threads while the Qt event loop
    # is still fully alive, then emit the signal.
    # Doing it the other way around risks hitting a
    # destroyed event dispatcher in worker.wait().
    # -------------------------------------------------

    def on_shutdown(self):
        self._cleanup_all_cards()
        self.shutdown_signal.emit()

    # -------------------------------------------------
    # DRAGGING
    # -------------------------------------------------

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.drag_pos = event.globalPosition().toPoint()

    def mouseMoveEvent(self, event):
        if not self.drag_pos.isNull():
            delta = (
                event.globalPosition().toPoint() - self.drag_pos
            )
            self.move(self.pos() + delta)
            self.drag_pos = event.globalPosition().toPoint()

    def mouseReleaseEvent(self, event):
        self.drag_pos = QPoint()


# =========================================================
# APP FACTORY
# =========================================================

def create_app():
    app     = QApplication(sys.argv)
    overlay = OverlayWindow()
    return app, overlay