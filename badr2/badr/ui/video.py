"""Minimal full-motion-video viewer (RTSP / HLS / HTTP) using QtMultimedia."""
from qgis.PyQt.QtCore import QUrl
from qgis.PyQt.QtWidgets import QDialog, QLabel, QVBoxLayout


class VideoWindow(QDialog):
    def __init__(self, url, title, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Video - {title}")
        self.resize(720, 460)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        try:                                   # Qt5
            from PyQt5.QtMultimedia import QMediaContent, QMediaPlayer
            from PyQt5.QtMultimediaWidgets import QVideoWidget
            self.view = QVideoWidget()
            self.player = QMediaPlayer(self, QMediaPlayer.StreamPlayback)
            self.player.setVideoOutput(self.view)
            self.player.setMedia(QMediaContent(QUrl(url)))
        except ImportError:                    # Qt6
            from PyQt6.QtMultimedia import QMediaPlayer
            from PyQt6.QtMultimediaWidgets import QVideoWidget
            self.view = QVideoWidget()
            self.player = QMediaPlayer(self)
            self.player.setVideoOutput(self.view)
            self.player.setSource(QUrl(url))
        lay.addWidget(self.view, 1)
        lay.addWidget(QLabel(f"  {url}"))
        self.player.play()

    def closeEvent(self, e):
        self.player.stop()
        super().closeEvent(e)
