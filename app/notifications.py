from PyQt5 import QtCore, QtWidgets, QtGui


class PopupToast(QtWidgets.QFrame):
    clicked = QtCore.pyqtSignal(dict)

    def __init__(self, parent=None):
        flags = QtCore.Qt.Tool | QtCore.Qt.FramelessWindowHint | QtCore.Qt.WindowStaysOnTopHint
        super().__init__(parent, flags)
        self.setAttribute(QtCore.Qt.WA_ShowWithoutActivating, True)
        self.setAttribute(QtCore.Qt.WA_TranslucentBackground, True)
        self._payload = None

        self.container = QtWidgets.QFrame(self)
        self.container.setObjectName("toastContainer")
        outer = QtWidgets.QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(self.container)
        layout = QtWidgets.QVBoxLayout(self.container)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(4)

        self.title_label = QtWidgets.QLabel("")
        title_font = QtGui.QFont("Segoe UI", 10, QtGui.QFont.Bold)
        self.title_label.setFont(title_font)
        layout.addWidget(self.title_label)

        self.body_label = QtWidgets.QLabel("")
        self.body_label.setWordWrap(True)
        layout.addWidget(self.body_label)

        shadow = QtWidgets.QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(18)
        shadow.setOffset(0, 4)
        shadow.setColor(QtGui.QColor(0, 0, 0, 80))
        self.container.setGraphicsEffect(shadow)
        self.container.setStyleSheet(
            "QFrame#toastContainer {"
            "  background: #ffffff;"
            "  border: 1px solid #e5e7eb;"
            "  border-radius: 10px;"
            "}"
            "QLabel { color: #111827; }"
        )

        self._timer = QtCore.QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self.hide)

    def show_message(self, title, body, payload=None, timeout_ms=5000):
        self._payload = dict(payload or {})
        self.title_label.setText(title or "")
        self.body_label.setText(body or "")
        self._position()
        self.show()
        self.raise_()
        QtCore.QTimer.singleShot(0, self._reposition_after_show)
        self._timer.start(timeout_ms)

    def _position(self):
        screen = QtWidgets.QApplication.screenAt(QtGui.QCursor.pos())
        if screen is None:
            screen = QtWidgets.QApplication.primaryScreen()
        if screen is None:
            return
        rect = screen.availableGeometry()
        geo = screen.geometry()
        if rect == geo:
            rect = QtCore.QRect(rect)
            rect.setHeight(max(0, rect.height() - 48))
        max_w = 320
        self.container.setFixedWidth(max_w)
        self.container.adjustSize()
        size = self.container.sizeHint()
        self.setFixedSize(size)
        self.adjustSize()
        margin = 16
        right_offset = 48
        x = rect.x() + rect.width() - size.width() - margin - right_offset
        y = rect.y() + rect.height() - size.height() - margin
        max_x = rect.x() + rect.width() - size.width() - margin
        max_y = rect.y() + rect.height() - size.height() - margin
        min_x = rect.x() + margin
        min_y = rect.y() + margin
        x = max(min_x, min(x, max_x))
        y = max(min_y, min(y, max_y))
        self.move(x, y)

    def _reposition_after_show(self):
        screen = QtWidgets.QApplication.screenAt(QtGui.QCursor.pos())
        if screen is None:
            screen = QtWidgets.QApplication.primaryScreen()
        if screen is None:
            return
        rect = screen.availableGeometry()
        geo = screen.geometry()
        if rect == geo:
            rect = QtCore.QRect(rect)
            rect.setHeight(max(0, rect.height() - 48))
        margin = 16
        right_offset = 48
        w = self.frameGeometry().width() or self.width()
        h = self.frameGeometry().height() or self.height()
        max_x = rect.x() + rect.width() - w - margin
        max_y = rect.y() + rect.height() - h - margin
        min_x = rect.x() + margin
        min_y = rect.y() + margin
        x = rect.x() + rect.width() - w - margin - right_offset
        y = rect.y() + rect.height() - h - margin
        x = max(min_x, min(x, max_x))
        y = max(min_y, min(y, max_y))
        self.move(x, y)

    def mousePressEvent(self, event):
        if self._payload is not None:
            self.clicked.emit(dict(self._payload))
        self.hide()
        super().mousePressEvent(event)


class PopupNotifier(QtCore.QObject):
    message_clicked = QtCore.pyqtSignal(dict)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._toast = PopupToast(None)
        self._toast.clicked.connect(self.message_clicked)

    def notify(self, title, body, payload=None, timeout_ms=5000):
        self._toast.show_message(title, body, payload=payload, timeout_ms=timeout_ms)


class WindowsNotifier(QtCore.QObject):
    message_clicked = QtCore.pyqtSignal(dict)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._tray = QtWidgets.QSystemTrayIcon(parent)
        self._tray.messageClicked.connect(self._on_message_clicked)
        self._tray.activated.connect(self._on_activated)
        self._last_payload = None
        self._menu = QtWidgets.QMenu()
        self._tray.setContextMenu(self._menu)

    def set_icon(self, icon):
        if isinstance(icon, QtGui.QIcon) and not icon.isNull():
            self._tray.setIcon(icon)

    def set_tooltip(self, text):
        if text:
            self._tray.setToolTip(text)

    def show(self):
        if not self._tray.isVisible():
            self._tray.show()

    def hide(self):
        self._tray.hide()

    def supports_messages(self):
        return QtWidgets.QSystemTrayIcon.supportsMessages()

    def notify(self, title, body, payload=None, timeout_ms=5000):
        self._last_payload = dict(payload or {})
        if not self._tray.isVisible():
            self._tray.show()
            QtCore.QTimer.singleShot(
                0,
                lambda: self._tray.showMessage(
                    title,
                    body,
                    QtWidgets.QSystemTrayIcon.Information,
                    timeout_ms
                )
            )
            return
        self._tray.showMessage(title, body, QtWidgets.QSystemTrayIcon.Information, timeout_ms)

    def _on_message_clicked(self):
        if self._last_payload is None:
            return
        self.message_clicked.emit(dict(self._last_payload))

    def _on_activated(self, reason):
        if reason == QtWidgets.QSystemTrayIcon.Trigger:
            self._on_message_clicked()
