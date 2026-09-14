"""
Generates the app icon and unread-count badge icons at runtime using
QPainter, so there's no external image asset to manage. Swap
`build_app_icon()` for a real designed icon whenever you have one.
"""

from PyQt6.QtCore import Qt, QRectF
from PyQt6.QtGui import QIcon, QPixmap, QPainter, QColor, QFont, QPainterPath


def build_app_icon(size: int = 256) -> QIcon:
    """A simple rounded-square 'M' mark in Messenger-ish blue/purple."""
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)

    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)

    path = QPainterPath()
    radius = size * 0.22
    path.addRoundedRect(QRectF(0, 0, size, size), radius, radius)

    painter.fillPath(path, QColor("#4A69FF"))

    font = QFont("Segoe UI", int(size * 0.5), QFont.Weight.Bold)
    painter.setFont(font)
    painter.setPen(QColor("white"))
    painter.drawText(pixmap.rect(), Qt.AlignmentFlag.AlignCenter, "M")

    painter.end()
    return QIcon(pixmap)


def build_badge_icon(count: int, size: int = 64) -> QIcon:
    """A small red circle with the unread count (or '9+') for the badge."""
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)

    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setBrush(QColor("#FA383E"))
    painter.setPen(Qt.PenStyle.NoPen)
    painter.drawEllipse(0, 0, size, size)

    text = str(count) if count <= 9 else "9+"
    font = QFont("Segoe UI", int(size * 0.42), QFont.Weight.Bold)
    painter.setFont(font)
    painter.setPen(QColor("white"))
    painter.drawText(pixmap.rect(), Qt.AlignmentFlag.AlignCenter, text)

    painter.end()
    return QIcon(pixmap)
