import sys
import os
import socket
import threading
import json
import re
import base64
import io
import time
import secrets
import queue
import getpass
from PyQt5 import QtCore, QtWidgets, QtGui

SERVER_HOST = "26.202.145.247"
SERVER_PORT = 5555
CLANS = ["Одмен", "Новичок", "Старик", "Старейшина", "Модер"]
CLAN_PRIMARY = {"Одмен", "Модер"}
CLAN_SECONDARY = {"Старейшина", "Новичок", "Старик"}


class AuthWorker(QtCore.QThread):
    success = QtCore.pyqtSignal(object, dict, str)
    error = QtCore.pyqtSignal(str)

    def __init__(self, host, port, username, password, email, mode, token=""):
        super().__init__()
        self.host = host
        self.port = port
        self.username = username
        self.password = password
        self.email = email
        self.mode = mode
        self.token = token

    def run(self):
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.connect((self.host, self.port))
            login_data = json.dumps({
                "action": self.mode,
                "username": self.username,
                "password": self.password,
                "email": self.email,
                "token": self.token
            }) + "\n"
            sock.send(login_data.encode())

            buffer = ""
            while "\n" not in buffer:
                data = sock.recv(1024).decode()
                if not data:
                    raise Exception("Server closed")
                buffer += data

            line, rest = buffer.split("\n", 1)
            resp = json.loads(line)

            if resp.get("type") != "auth":
                raise Exception("Auth failed")

            if resp.get("status") == "need_code":
                self.success.emit(sock, resp, "")
                return

            if resp.get("status") != "ok":
                msg = resp.get("message", "Не удалось войти")
                sock.close()
                self.error.emit(msg)
                return

            self.success.emit(sock, resp, rest)
        except Exception as e:
            self.error.emit(str(e))


class ReceiverThread(QtCore.QThread):
    message_received = QtCore.pyqtSignal(dict)
    connection_lost = QtCore.pyqtSignal()

    def __init__(self, sock, prebuffer=""):
        super().__init__()
        self.sock = sock
        self._running = True
        self._prebuffer = prebuffer

    def stop(self):
        self._running = False

    def run(self):
        buffer = self._prebuffer
        while self._running:
            try:
                data = self.sock.recv(1024).decode()
                if not data:
                    break
                buffer += data
                while "\n" in buffer:
                    line, buffer = buffer.split("\n", 1)
                    if not line.strip():
                        continue
                    msg = json.loads(line)
                    self.message_received.emit(msg)
            except Exception:
                break
        self.connection_lost.emit()


class MessageInput(QtWidgets.QTextEdit):
    send_requested = QtCore.pyqtSignal()

    def keyPressEvent(self, event):
        if event.key() in (QtCore.Qt.Key_Return, QtCore.Qt.Key_Enter):
            if event.modifiers() == QtCore.Qt.NoModifier:
                self.send_requested.emit()
                return
        super().keyPressEvent(event)


class AvatarLabel(QtWidgets.QLabel):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._pixmap = None
        self._avatar_data = ""
        self._pending_token = 0

    @QtCore.pyqtSlot(QtGui.QImage, int)
    def _on_avatar_decoded(self, image, token):
        if token != self._pending_token:
            return
        if image is None or image.isNull():
            self._pixmap = None
        else:
            self._pixmap = QtGui.QPixmap.fromImage(image)
        self.update()

    def set_avatar_data(self, data_url):
        if data_url == self._avatar_data:
            return
        self._avatar_data = data_url or ""
        self._pixmap = None
        self._pending_token += 1
        token = self._pending_token

        if not self._avatar_data or not isinstance(self._avatar_data, str):
            self.update()
            return
        if not self._avatar_data.startswith("data:image/"):
            self.update()
            return

        runnable = AvatarDecodeRunnable(self._avatar_data, token, self)
        QtCore.QThreadPool.globalInstance().start(runnable)

    def paintEvent(self, event):
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.Antialiasing)
        rect = self.rect().adjusted(1, 1, -1, -1)

        border = QtGui.QPen(QtGui.QColor("#e5e7eb"))
        border.setWidth(1)
        painter.setPen(border)
        painter.setBrush(QtGui.QColor("#ffffff"))
        painter.drawEllipse(rect)

        if self._pixmap:
            clip_path = QtGui.QPainterPath()
            clip_path.addEllipse(QtCore.QRectF(rect))
            painter.setClipPath(clip_path)
            scaled = self._pixmap.scaled(
                rect.size(),
                QtCore.Qt.KeepAspectRatioByExpanding,
                QtCore.Qt.SmoothTransformation
            )
            painter.drawPixmap(rect, scaled, scaled.rect())
        else:
            painter.setPen(QtGui.QColor("#9ca3af"))
            painter.setFont(self.font())
            painter.drawText(rect, QtCore.Qt.AlignCenter, self.text())


class SenderThread(QtCore.QThread):
    error = QtCore.pyqtSignal(str)

    def __init__(self, sock):
        super().__init__()
        self.sock = sock
        self._running = True
        self._queue = []
        self._lock = threading.Lock()
        self._event = threading.Event()

    def stop(self):
        self._running = False
        self._event.set()

    def send(self, payload):
        data = json.dumps(payload) + "\n"
        with self._lock:
            self._queue.append(data)
        self._event.set()

    def run(self):
        while self._running:
            self._event.wait()
            self._event.clear()
            while True:
                with self._lock:
                    if not self._queue:
                        break
                    data = self._queue.pop(0)
                try:
                    self.sock.sendall(data.encode())
                except Exception as e:
                    self.error.emit(str(e))
                    return


class FileUploadWorker(QtCore.QThread):
    progress = QtCore.pyqtSignal(int, int)
    finished = QtCore.pyqtSignal(str)
    error = QtCore.pyqtSignal(str)

    def __init__(self, file_id, file_path, total_size, send_func, ready_event, cancel_event, status_ref):
        super().__init__()
        self.file_id = file_id
        self.file_path = file_path
        self.total_size = total_size
        self.send_func = send_func
        self.ready_event = ready_event
        self.cancel_event = cancel_event
        self.status_ref = status_ref

    def run(self):
        if not self.ready_event.wait(10):
            self.error.emit("Сервер не подтвердил загрузку")
            return
        if not self.status_ref.get("allowed", False):
            self.error.emit(self.status_ref.get("error", "Загрузка отклонена"))
            return
        if self.cancel_event.is_set():
            return
        try:
            sent = 0
            with open(self.file_path, "rb") as f:
                while not self.cancel_event.is_set():
                    chunk = f.read(32 * 1024)
                    if not chunk:
                        break
                    payload = {
                        "action": "file_upload_chunk",
                        "file_id": self.file_id,
                        "data": base64.b64encode(chunk).decode("ascii")
                    }
                    self.send_func(payload)
                    sent += len(chunk)
                    self.progress.emit(sent, self.total_size)
            if self.cancel_event.is_set():
                return
            self.send_func({"action": "file_upload_end", "file_id": self.file_id})
            self.finished.emit(self.file_id)
        except Exception as e:
            self.error.emit(str(e))


class FileDownloadWorker(QtCore.QThread):
    progress = QtCore.pyqtSignal(int, int)
    finished = QtCore.pyqtSignal(str)
    error = QtCore.pyqtSignal(str)

    def __init__(self, file_id, target_path, total_size):
        super().__init__()
        self.file_id = file_id
        self.target_path = target_path
        self.total_size = total_size
        self._queue = queue.Queue()
        self._running = True

    def add_chunk(self, data):
        if self._running:
            self._queue.put(data)

    def finish(self):
        self._running = False
        self._queue.put(None)

    def run(self):
        received = 0
        try:
            os.makedirs(os.path.dirname(self.target_path), exist_ok=True)
            with open(self.target_path, "wb") as f:
                while True:
                    item = self._queue.get()
                    if item is None:
                        break
                    f.write(item)
                    received += len(item)
                    self.progress.emit(received, self.total_size)
            self.finished.emit(self.file_id)
        except Exception as e:
            self.error.emit(str(e))


class AvatarDecodeRunnable(QtCore.QRunnable):
    def __init__(self, data_url, token, target):
        super().__init__()
        self.data_url = data_url
        self.token = token
        self.target = target

    def run(self):
        image = QtGui.QImage()
        try:
            _, b64 = self.data_url.split(",", 1)
            raw = base64.b64decode(b64)
            image.loadFromData(raw)
        except Exception:
            image = QtGui.QImage()
        QtCore.QMetaObject.invokeMethod(
            self.target,
            "_on_avatar_decoded",
            QtCore.Qt.QueuedConnection,
            QtCore.Q_ARG(QtGui.QImage, image),
            QtCore.Q_ARG(int, self.token)
        )


class AvatarEncodeWorker(QtCore.QThread):
    completed = QtCore.pyqtSignal(str)
    failed = QtCore.pyqtSignal(str)

    def __init__(self, file_path, max_side=256, max_bytes=250_000):
        super().__init__()
        self.file_path = file_path
        self.max_side = max_side
        self.max_bytes = max_bytes

    def run(self):
        try:
            pixmap = QtGui.QPixmap(self.file_path)
            if pixmap.isNull():
                self.failed.emit("Не удалось загрузить изображение")
                return
            if pixmap.width() > self.max_side or pixmap.height() > self.max_side:
                pixmap = pixmap.scaled(
                    self.max_side,
                    self.max_side,
                    QtCore.Qt.KeepAspectRatio,
                    QtCore.Qt.SmoothTransformation
                )
            byte_array = QtCore.QByteArray()
            buffer = QtCore.QBuffer(byte_array)
            buffer.open(QtCore.QIODevice.WriteOnly)
            pixmap.save(buffer, "PNG")
            data = byte_array.data()
            if len(data) > self.max_bytes:
                self.failed.emit("Слишком большой размер файла. Пожалуйста, уменьшите изображение.")
                return
            b64 = base64.b64encode(data).decode("ascii")
            self.completed.emit(f"data:image/png;base64,{b64}")
        except Exception:
            self.failed.emit("Ошибка обработки файла")


class ProfileDialog(QtWidgets.QDialog):
    def __init__(self, parent, owner_username, target_username, is_self, is_admin, clans):
        super().__init__(parent)
        self.owner_username = owner_username
        self.target_username = target_username
        self.is_self = is_self
        self.is_admin = is_admin
        self.clans = list(clans or [])
        self.avatar_data = ""

        self.setWindowTitle("Профиль пользователя")
        self.setModal(True)
        self.setMinimumWidth(360)

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        header = QtWidgets.QHBoxLayout()
        header.setSpacing(10)

        self.avatar_label = AvatarLabel(self)
        self.avatar_label.setFixedSize(72, 72)
        font = self.avatar_label.font()
        font.setBold(True)
        font.setPixelSize(18)
        self.avatar_label.setFont(font)
        self.avatar_label.setAlignment(QtCore.Qt.AlignCenter)
        header.addWidget(self.avatar_label)

        title_box = QtWidgets.QVBoxLayout()
        title_row = QtWidgets.QHBoxLayout()
        title_row.setSpacing(6)
        self.title_label = QtWidgets.QLabel("")
        title_font = QtGui.QFont("Segoe UI", 14, QtGui.QFont.Bold)
        self.title_label.setFont(title_font)
        title_row.addWidget(self.title_label)

        self.profile_clan_primary = QtWidgets.QLabel("")
        self.profile_clan_primary.setObjectName("clanPrimaryLabel")
        self.profile_clan_primary.setVisible(False)
        title_row.addWidget(self.profile_clan_primary)

        self.profile_clan_dot = QtWidgets.QLabel("•")
        self.profile_clan_dot.setObjectName("clanDotLabel")
        self.profile_clan_dot.setVisible(False)
        title_row.addWidget(self.profile_clan_dot)

        self.profile_clan_secondary = QtWidgets.QLabel("")
        self.profile_clan_secondary.setObjectName("clanSecondaryLabel")
        self.profile_clan_secondary.setVisible(False)
        title_row.addWidget(self.profile_clan_secondary)
        title_row.addStretch(1)
        title_box.addLayout(title_row)

        self.username_label = QtWidgets.QLabel(f"@{self.target_username}")
        self.username_label.setObjectName("mutedLabel")
        title_box.addWidget(self.username_label)
        title_box.addStretch(1)

        header.addLayout(title_box, 1)

        if self.is_self:
            self.change_avatar_btn = QtWidgets.QPushButton("Сменить аватар")
            self.change_avatar_btn.setObjectName("ghostButton")
            header.addWidget(self.change_avatar_btn, 0, QtCore.Qt.AlignTop)
        else:
            self.change_avatar_btn = None

        layout.addLayout(header)

        nick_label = QtWidgets.QLabel("Никнейм")
        nick_label.setObjectName("mutedLabel")
        layout.addWidget(nick_label)
        self.nick_input = QtWidgets.QLineEdit()
        self.nick_input.setPlaceholderText("Никнейм")
        self.nick_input.setReadOnly(not self.is_self)
        layout.addWidget(self.nick_input)

        about_label = QtWidgets.QLabel("О себе")
        about_label.setObjectName("mutedLabel")
        layout.addWidget(about_label)
        self.about_input = QtWidgets.QTextEdit()
        self.about_input.setPlaceholderText("Расскажите о себе")
        self.about_input.setFixedHeight(90)
        self.about_input.setReadOnly(not self.is_self)
        layout.addWidget(self.about_input)

        self.admin_box = QtWidgets.QWidget()
        admin_layout = QtWidgets.QHBoxLayout(self.admin_box)
        admin_layout.setContentsMargins(0, 0, 0, 0)
        admin_layout.setSpacing(8)
        self.clan_input = QtWidgets.QLineEdit()
        self.clan_input.setPlaceholderText("Новый клан")
        self.clan_input.setObjectName("input")
        self.clan_combo = QtWidgets.QComboBox()
        self.clan_combo.addItems(self.clans)
        self.clan_level = QtWidgets.QComboBox()
        self.clan_level.addItems(["Клан (1 уровень)", "Подклан (2 уровень)"])
        self.set_clan_btn = QtWidgets.QPushButton("Назначить клан")
        self.set_clan_btn.setObjectName("ghostButton")
        admin_layout.addWidget(self.clan_input, 2)
        admin_layout.addWidget(self.clan_combo, 1)
        admin_layout.addWidget(self.clan_level, 0)
        admin_layout.addWidget(self.set_clan_btn)
        self.admin_box.setVisible(self.is_admin)
        layout.addWidget(self.admin_box)

        note_label = QtWidgets.QLabel("Заметка (только для вас)")
        note_label.setObjectName("mutedLabel")
        layout.addWidget(note_label)
        self.note_input = QtWidgets.QTextEdit()
        self.note_input.setPlaceholderText("Личная заметка")
        self.note_input.setFixedHeight(90)
        layout.addWidget(self.note_input)

        actions = QtWidgets.QHBoxLayout()
        actions.addStretch(1)

        self.save_note_btn = QtWidgets.QPushButton("Сохранить заметку")
        self.save_note_btn.setObjectName("ghostButton")
        actions.addWidget(self.save_note_btn)

        if self.is_self:
            self.save_profile_btn = QtWidgets.QPushButton("Сохранить профиль")
            self.save_profile_btn.setObjectName("primaryButton")
            actions.addWidget(self.save_profile_btn)
        else:
            self.save_profile_btn = None

        self.close_btn = QtWidgets.QPushButton("Закрыть")
        self.close_btn.setObjectName("primaryButton")
        self.close_btn.clicked.connect(self.accept)
        actions.addWidget(self.close_btn)

        layout.addLayout(actions)

    def set_profile(self, profile):
        nick = (profile.get("nick") or "").strip()
        about = profile.get("about") or ""
        avatar = profile.get("avatar") or ""
        clan_primary = (profile.get("clan_primary") or "").strip()
        clan_secondary = (profile.get("clan_secondary") or "").strip()
        self.nick_input.setText(nick)
        self.about_input.setPlainText(about)
        self.set_avatar_data(avatar)
        self._set_clan_display(clan_primary, clan_secondary)
        self._update_title()

    def set_note(self, note):
        self.note_input.setPlainText(note or "")

    def _update_title(self):
        nick = (self.nick_input.text() or "").strip()
        display = nick if nick else self.target_username
        self.title_label.setText(display)
        initials = display[:1].upper() if display else "?"
        self.avatar_label.setText(initials)

    def set_avatar_data(self, data_url):
        self.avatar_data = data_url or ""
        self.avatar_label.set_avatar_data(self.avatar_data)

    def _set_clan_display(self, clan_primary, clan_secondary):
        primary = (clan_primary or "").strip()
        secondary = (clan_secondary or "").strip()
        if primary in self.clans:
            self.clan_combo.setCurrentText(primary)
        elif secondary in self.clans:
            self.clan_combo.setCurrentText(secondary)
        if primary:
            self.profile_clan_primary.setText(primary)
            self.profile_clan_primary.setVisible(True)
        else:
            self.profile_clan_primary.setVisible(False)
        if secondary:
            self.profile_clan_secondary.setText(secondary)
            self.profile_clan_secondary.setVisible(True)
        else:
            self.profile_clan_secondary.setVisible(False)
        self.profile_clan_dot.setVisible(bool(primary and secondary))

    def collect_profile_data(self):
        return {
            "nick": (self.nick_input.text() or "").strip(),
            "about": self.about_input.toPlainText().strip(),
            "avatar": self.avatar_data
        }

    def collect_note(self):
        return self.note_input.toPlainText().strip()

    def set_admin(self, is_admin):
        self.is_admin = bool(is_admin)
        self.admin_box.setVisible(self.is_admin)

    def selected_clan(self):
        manual = (self.clan_input.text() or "").strip()
        if manual:
            return manual
        return (self.clan_combo.currentText() or "").strip()

    def selected_clan_level(self):
        label = (self.clan_level.currentText() or "").strip().lower()
        if "1" in label:
            return "primary"
        return "secondary"


class MAXutkoClient(QtWidgets.QMainWindow):

    def __init__(self):
        super().__init__()

        self.setWindowTitle("MAXutko Messenger")
        self.resize(900, 700)
        self.setMinimumSize(900, 700)

        # Socket
        self.client_socket = None
        self.running = False
        self.prebuffer = ""

        self.host = SERVER_HOST
        self.port = SERVER_PORT
        self.username = ""
        self.password = ""
        self.email = ""
        self.auth_mode = "login"
        self.pending_socket = None
        self.receiver_thread = None
        self.sender_thread = None
        self.auth_worker = None
        self._reset_pending_code = ""
        self._reset_pending_username = ""
        self._reset_pending_email = ""
        self.chat_mode = "group"
        self.dm_target = None
        self.conversations = {"group": []}
        self.message_index = {}
        self.unread_counts = {}
        self.contact_items = []
        self.search_items = []
        self.search_query = ""
        self.latest_users = []
        self.user_directory = {}
        self.my_clan_primary = ""
        self.my_clan_secondary = ""
        self.clans = list(CLANS)
        self.profile_dialogs = {}
        self.avatar_cache_path = os.path.join(os.path.dirname(__file__), "avatar_cache.json")
        self.avatar_cache = self._load_avatar_cache()
        self.auth_token_path = os.path.join(os.path.dirname(__file__), "auth_token.json")
        self.auth_tokens_path = os.path.join(os.path.dirname(__file__), "auth_tokens.json")
        self.auth_tokens = self._load_saved_tokens()
        self.search_timer = QtCore.QTimer(self)
        self.search_timer.setSingleShot(True)
        self.search_timer.setInterval(300)
        self.search_timer.timeout.connect(self._send_search_request)
        self.empty_state_label = None
        self.pending_contact_adds = set()
        self.reply_to_id = None
        self.upload_states = {}
        self.upload_states_lock = threading.Lock()
        self.download_states = {}
        self.download_states_lock = threading.Lock()
        self.downloaded_files = {}
        self.file_button_refs = {}
        self.downloads_dir = os.path.join(os.path.expanduser("~"), "Downloads")

        self.setup_ui()
        self.show_login_screen()
        self._attempt_token_login()

    # ================= UI =================

    def setup_ui(self):
        self.central = QtWidgets.QWidget()
        self.setCentralWidget(self.central)

        self.layout = QtWidgets.QVBoxLayout(self.central)
        self.layout.setContentsMargins(20, 20, 20, 20)

        self.stack = QtWidgets.QStackedWidget()
        self.layout.addWidget(self.stack)

        self.login_page = QtWidgets.QWidget()
        self.reset_step1_page = QtWidgets.QWidget()
        self.reset_step2_page = QtWidgets.QWidget()
        self.chat_page = QtWidgets.QWidget()
        self.stack.addWidget(self.login_page)
        self.stack.addWidget(self.reset_step1_page)
        self.stack.addWidget(self.reset_step2_page)
        self.stack.addWidget(self.chat_page)

        self._build_login_page()
        self._build_reset_step1_page()
        self._build_reset_step2_page()
        self._build_chat_page()

        self._load_qss()

    def _load_qss(self):
        base_dir = QtCore.QFileInfo(__file__).absolutePath()
        candidates = [
            os.path.join(base_dir, "ui.qss"),
            os.path.join(os.getcwd(), "ui.qss"),
            os.path.join(os.getcwd(), "app", "ui.qss"),
        ]
        for qss_path in candidates:
            if os.path.isfile(qss_path):
                try:
                    with open(qss_path, "r", encoding="utf-8") as f:
                        QtWidgets.QApplication.instance().setStyleSheet(f.read())
                    return
                except Exception as e:
                    print(f"Failed to load QSS: {qss_path} ({e})", file=sys.stderr)
                    return
        print("QSS not found. Tried: " + ", ".join(candidates), file=sys.stderr)

    def _build_login_page(self):
        root_layout = QtWidgets.QVBoxLayout(self.login_page)
        root_layout.setAlignment(QtCore.Qt.AlignCenter)

        card = QtWidgets.QFrame()
        card.setObjectName("loginCard")
        card_layout = QtWidgets.QVBoxLayout(card)
        card_layout.setContentsMargins(24, 22, 24, 22)
        card_layout.setSpacing(12)

        title = QtWidgets.QLabel("MAXutko")
        title.setObjectName("titleLabel")
        title.setAlignment(QtCore.Qt.AlignCenter)
        title_font = QtGui.QFont("Segoe UI", 24, QtGui.QFont.Bold)
        title.setFont(title_font)
        card_layout.addWidget(title)

        # Username
        self.username_input = QtWidgets.QLineEdit()
        self.username_input.setPlaceholderText("Логин или никнейм")
        self.username_input.setObjectName("input")

        # Password
        self.password_input = QtWidgets.QLineEdit()
        self.password_input.setPlaceholderText("Пароль")
        self.password_input.setEchoMode(QtWidgets.QLineEdit.Password)
        self.password_input.setObjectName("input")

        # Email
        self.email_row = QtWidgets.QWidget()
        email_layout = QtWidgets.QHBoxLayout(self.email_row)
        email_layout.setContentsMargins(0, 0, 0, 0)
        email_layout.setSpacing(8)
        self.email_input = QtWidgets.QLineEdit()
        self.email_input.setPlaceholderText("Почта для подтверждения")
        self.email_input.setObjectName("input")
        email_layout.addWidget(self._labeled("Email:", self.email_input))

        card_layout.addWidget(self._labeled("Username:", self.username_input))
        card_layout.addWidget(self._labeled("Password:", self.password_input))
        card_layout.addWidget(self.email_row)

        # Mode
        mode_row = QtWidgets.QWidget()
        mode_layout = QtWidgets.QHBoxLayout(mode_row)
        mode_layout.setContentsMargins(0, 0, 0, 0)
        mode_layout.setSpacing(12)

        self.login_radio = QtWidgets.QRadioButton("Вход")
        self.register_radio = QtWidgets.QRadioButton("Регистрация")
        self.login_radio.setChecked(True)
        self.mode_group = QtWidgets.QButtonGroup()
        self.mode_group.addButton(self.login_radio)
        self.mode_group.addButton(self.register_radio)
        self.login_radio.toggled.connect(self._sync_auth_mode)

        mode_layout.addWidget(self.login_radio)
        mode_layout.addWidget(self.register_radio)
        mode_layout.addStretch(1)
        card_layout.addWidget(mode_row)

        # Remember me
        self.remember_checkbox = QtWidgets.QCheckBox("Запомнить меня")
        self.remember_checkbox.setObjectName("mutedLabel")
        card_layout.addWidget(self.remember_checkbox)

        # Button
        self.connect_btn = QtWidgets.QPushButton("Войти")
        self.connect_btn.setObjectName("primaryButton")
        self.connect_btn.clicked.connect(self.connect_to_server)
        card_layout.addWidget(self.connect_btn)

        # Reset password button
        self.reset_btn = QtWidgets.QPushButton("Сменить пароль")
        self.reset_btn.setObjectName("ghostButton")
        self.reset_btn.clicked.connect(self.show_reset_step1_screen)
        reset_row = QtWidgets.QHBoxLayout()
        reset_row.setContentsMargins(0, 0, 0, 0)
        reset_row.addWidget(self.reset_btn, 0, QtCore.Qt.AlignLeft)
        reset_row.addStretch(1)
        card_layout.addLayout(reset_row)

        # Status
        self.status_label = QtWidgets.QLabel("")
        self.status_label.setObjectName("statusLabel")
        self.status_label.setAlignment(QtCore.Qt.AlignCenter)
        card_layout.addWidget(self.status_label)

        # Code verify
        self.code_row = QtWidgets.QWidget()
        code_layout = QtWidgets.QHBoxLayout(self.code_row)
        code_layout.setContentsMargins(0, 0, 0, 0)
        code_layout.setSpacing(8)
        self.code_input = QtWidgets.QLineEdit()
        self.code_input.setPlaceholderText("Код из письма")
        self.code_input.setObjectName("input")
        self.verify_btn = QtWidgets.QPushButton("Подтвердить")
        self.verify_btn.setObjectName("primaryButton")
        self.verify_btn.clicked.connect(self.verify_code)
        code_layout.addWidget(self._labeled("Code:", self.code_input))
        code_layout.addWidget(self.verify_btn)
        card_layout.addWidget(self.code_row)

        root_layout.addWidget(card, 0, QtCore.Qt.AlignCenter)

        self.email_row.setVisible(False)
        self.code_row.setVisible(False)

    def _build_reset_step1_page(self):
        root_layout = QtWidgets.QVBoxLayout(self.reset_step1_page)
        root_layout.setAlignment(QtCore.Qt.AlignCenter)

        card = QtWidgets.QFrame()
        card.setObjectName("loginCard")
        card_layout = QtWidgets.QVBoxLayout(card)
        card_layout.setContentsMargins(24, 22, 24, 22)
        card_layout.setSpacing(12)

        title = QtWidgets.QLabel("Смена пароля")
        title.setObjectName("titleLabel")
        title.setAlignment(QtCore.Qt.AlignCenter)
        title_font = QtGui.QFont("Segoe UI", 18, QtGui.QFont.Bold)
        title.setFont(title_font)
        card_layout.addWidget(title)

        self.reset_username_input = QtWidgets.QLineEdit()
        self.reset_username_input.setPlaceholderText("Логин")
        self.reset_username_input.setObjectName("input")

        self.reset_email_input = QtWidgets.QLineEdit()
        self.reset_email_input.setPlaceholderText("Почта")
        self.reset_email_input.setObjectName("input")

        card_layout.addWidget(self._labeled("Username:", self.reset_username_input))
        card_layout.addWidget(self._labeled("Email:", self.reset_email_input))

        self.reset_send_btn = QtWidgets.QPushButton("Отправить код")
        self.reset_send_btn.setObjectName("primaryButton")
        self.reset_send_btn.clicked.connect(self._request_password_reset)
        card_layout.addWidget(self.reset_send_btn)

        self.reset_status_label = QtWidgets.QLabel("")
        self.reset_status_label.setObjectName("statusLabel")
        self.reset_status_label.setAlignment(QtCore.Qt.AlignCenter)
        card_layout.addWidget(self.reset_status_label)

        self.reset_code_row = QtWidgets.QWidget()
        code_layout = QtWidgets.QHBoxLayout(self.reset_code_row)
        code_layout.setContentsMargins(0, 0, 0, 0)
        code_layout.setSpacing(8)
        self.reset_code_input = QtWidgets.QLineEdit()
        self.reset_code_input.setPlaceholderText("Код из письма")
        self.reset_code_input.setObjectName("input")
        self.reset_verify_btn = QtWidgets.QPushButton("Подтвердить код")
        self.reset_verify_btn.setObjectName("primaryButton")
        self.reset_verify_btn.clicked.connect(self._verify_reset_code_step1)
        code_layout.addWidget(self._labeled("Code:", self.reset_code_input))
        code_layout.addWidget(self.reset_verify_btn)
        card_layout.addWidget(self.reset_code_row)

        self.reset_resend_btn = QtWidgets.QPushButton("Отправить код повторно")
        self.reset_resend_btn.setObjectName("ghostButton")
        self.reset_resend_btn.clicked.connect(self._resend_reset_code)
        card_layout.addWidget(self.reset_resend_btn)

        self.reset_back_btn = QtWidgets.QPushButton("Назад к входу")
        self.reset_back_btn.setObjectName("ghostButton")
        self.reset_back_btn.clicked.connect(self.show_login_screen)
        back_row = QtWidgets.QHBoxLayout()
        back_row.setContentsMargins(0, 0, 0, 0)
        back_row.addWidget(self.reset_back_btn, 0, QtCore.Qt.AlignLeft)
        back_row.addStretch(1)
        card_layout.addLayout(back_row)

        root_layout.addWidget(card, 0, QtCore.Qt.AlignCenter)

        self.reset_code_row.setVisible(False)
        self.reset_resend_btn.setVisible(False)

    def _build_reset_step2_page(self):
        root_layout = QtWidgets.QVBoxLayout(self.reset_step2_page)
        root_layout.setAlignment(QtCore.Qt.AlignCenter)

        card = QtWidgets.QFrame()
        card.setObjectName("loginCard")
        card_layout = QtWidgets.QVBoxLayout(card)
        card_layout.setContentsMargins(24, 22, 24, 22)
        card_layout.setSpacing(12)

        title = QtWidgets.QLabel("Новый пароль")
        title.setObjectName("titleLabel")
        title.setAlignment(QtCore.Qt.AlignCenter)
        title_font = QtGui.QFont("Segoe UI", 18, QtGui.QFont.Bold)
        title.setFont(title_font)
        card_layout.addWidget(title)

        self.reset_new_password_input = QtWidgets.QLineEdit()
        self.reset_new_password_input.setPlaceholderText("Новый пароль")
        self.reset_new_password_input.setEchoMode(QtWidgets.QLineEdit.Password)
        self.reset_new_password_input.setObjectName("input")
        card_layout.addWidget(self._labeled("Password:", self.reset_new_password_input))

        self.reset_change_btn = QtWidgets.QPushButton("Сменить пароль")
        self.reset_change_btn.setObjectName("dangerButton")
        self.reset_change_btn.clicked.connect(self._verify_reset_code_step2)
        card_layout.addWidget(self.reset_change_btn)

        self.reset_step2_status = QtWidgets.QLabel("")
        self.reset_step2_status.setObjectName("statusLabel")
        self.reset_step2_status.setAlignment(QtCore.Qt.AlignCenter)
        card_layout.addWidget(self.reset_step2_status)

        self.reset_step2_back_btn = QtWidgets.QPushButton("Назад к коду")
        self.reset_step2_back_btn.setObjectName("ghostButton")
        self.reset_step2_back_btn.clicked.connect(self.show_reset_step1_screen)
        back_row = QtWidgets.QHBoxLayout()
        back_row.setContentsMargins(0, 0, 0, 0)
        back_row.addWidget(self.reset_step2_back_btn, 0, QtCore.Qt.AlignLeft)
        back_row.addStretch(1)
        card_layout.addLayout(back_row)

        root_layout.addWidget(card, 0, QtCore.Qt.AlignCenter)

    def _build_chat_page(self):
        layout = QtWidgets.QVBoxLayout(self.chat_page)
        layout.setSpacing(10)
        layout.setContentsMargins(0, 0, 0, 0)

        # Top bar
        top_bar = QtWidgets.QFrame()
        top_bar.setObjectName("topBar")
        top_layout = QtWidgets.QHBoxLayout(top_bar)
        top_layout.setContentsMargins(12, 8, 12, 8)

        self.user_info = QtWidgets.QWidget()
        user_layout = QtWidgets.QHBoxLayout(self.user_info)
        user_layout.setContentsMargins(0, 0, 0, 0)
        user_layout.setSpacing(8)

        self.self_avatar = AvatarLabel(self.user_info)
        self.self_avatar.setObjectName("selfAvatar")
        self.self_avatar.setFixedSize(40, 40)
        avatar_font = self.self_avatar.font()
        avatar_font.setPixelSize(14)
        avatar_font.setBold(True)
        self.self_avatar.setFont(avatar_font)
        self.self_avatar.setAlignment(QtCore.Qt.AlignCenter)
        user_layout.addWidget(self.self_avatar, 0, QtCore.Qt.AlignVCenter)

        user_text = QtWidgets.QVBoxLayout()
        user_text.setContentsMargins(0, 0, 0, 0)
        user_text.setSpacing(2)

        self.user_label = QtWidgets.QLabel("")
        self.user_label.setObjectName("userNameLabel")
        name_font = QtGui.QFont("Segoe UI", 11, QtGui.QFont.Bold)
        self.user_label.setFont(name_font)
        user_text.addWidget(self.user_label)

        self.user_meta_label = QtWidgets.QLabel("")
        self.user_meta_label.setObjectName("userMetaLabel")
        user_text.addWidget(self.user_meta_label)

        self.user_clan_row = QtWidgets.QHBoxLayout()
        self.user_clan_row.setSpacing(6)
        self.user_clan_primary = QtWidgets.QLabel("")
        self.user_clan_primary.setObjectName("clanPrimaryLabel")
        self.user_clan_primary.setVisible(False)
        self.user_clan_row.addWidget(self.user_clan_primary)
        self.user_clan_dot = QtWidgets.QLabel("•")
        self.user_clan_dot.setObjectName("clanDotLabel")
        self.user_clan_dot.setVisible(False)
        self.user_clan_row.addWidget(self.user_clan_dot)
        self.user_clan_secondary = QtWidgets.QLabel("")
        self.user_clan_secondary.setObjectName("clanSecondaryLabel")
        self.user_clan_secondary.setVisible(False)
        self.user_clan_row.addWidget(self.user_clan_secondary)
        self.user_clan_row.addStretch(1)
        user_text.addLayout(self.user_clan_row)

        user_layout.addLayout(user_text)
        top_layout.addWidget(self.user_info)

        self.chat_label = QtWidgets.QLabel("")
        self.chat_label.setObjectName("chatTitle")
        chat_font = QtGui.QFont("Segoe UI", 11, QtGui.QFont.Bold)
        self.chat_label.setFont(chat_font)
        top_layout.addWidget(self.chat_label)

        self.back_btn = QtWidgets.QPushButton("Назад в общий чат")
        self.back_btn.setObjectName("ghostButton")
        self.back_btn.clicked.connect(self.back_to_group)
        self.back_btn.setVisible(False)
        top_layout.addWidget(self.back_btn)

        top_layout.addStretch(1)

        self.accounts_btn = QtWidgets.QPushButton("Аккаунты")
        self.accounts_btn.setObjectName("ghostButton")
        self.accounts_btn.clicked.connect(self._show_accounts_menu)
        top_layout.addWidget(self.accounts_btn)

        self.disconnect_btn = QtWidgets.QPushButton("Выйти")
        self.disconnect_btn.setObjectName("dangerButton")
        self.disconnect_btn.clicked.connect(self.disconnect)
        top_layout.addWidget(self.disconnect_btn)

        layout.addWidget(top_bar)

        # Center area (messages + users)
        center = QtWidgets.QWidget()
        center_layout = QtWidgets.QHBoxLayout(center)
        center_layout.setContentsMargins(0, 0, 0, 0)
        center_layout.setSpacing(10)

        # Users panel
        self.users_panel = QtWidgets.QFrame()
        self.users_panel.setObjectName("usersPanel")
        self.users_panel.setFixedWidth(240)
        users_layout = QtWidgets.QVBoxLayout(self.users_panel)
        users_layout.setContentsMargins(10, 10, 10, 10)
        users_layout.setSpacing(8)

        self.users_title = QtWidgets.QLabel("Контакты")
        self.users_title.setObjectName("usersTitle")
        users_layout.addWidget(self.users_title)

        self.users_search = QtWidgets.QLineEdit()
        self.users_search.setPlaceholderText("Поиск по никнейму")
        self.users_search.setObjectName("input")
        self.users_search.textChanged.connect(self._on_search_text_changed)
        users_layout.addWidget(self.users_search)

        self.empty_state_label = QtWidgets.QLabel("")
        self.empty_state_label.setObjectName("mutedLabel")
        self.empty_state_label.setAlignment(QtCore.Qt.AlignCenter)
        self.empty_state_label.setWordWrap(True)
        self.empty_state_label.setVisible(False)
        users_layout.addWidget(self.empty_state_label)

        self.users_list = QtWidgets.QListWidget()
        self.users_list.setObjectName("usersList")
        self.users_list.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
        self.users_list.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        self.users_list.itemDoubleClicked.connect(self.open_direct_chat)
        self.users_list.currentItemChanged.connect(self._sync_user_selection)
        self.users_list.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)
        self.users_list.customContextMenuRequested.connect(self.show_user_menu)
        users_layout.addWidget(self.users_list, 1)

        center_layout.addWidget(self.users_panel)

        # Messages
        self.scroll_area = QtWidgets.QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setObjectName("messagesScroll")

        self.messages_widget = QtWidgets.QWidget()
        self.messages_widget.setObjectName("messagesContainer")
        self.messages_layout = QtWidgets.QVBoxLayout(self.messages_widget)
        self.messages_layout.setContentsMargins(10, 10, 10, 10)
        self.messages_layout.setSpacing(8)
        self.messages_layout.addStretch(1)

        self.scroll_area.setWidget(self.messages_widget)
        center_layout.addWidget(self.scroll_area, 1)
        layout.addWidget(center, 1)

        # Bottom
        bottom = QtWidgets.QWidget()
        bottom_layout = QtWidgets.QVBoxLayout(bottom)
        bottom_layout.setContentsMargins(0, 0, 0, 0)
        bottom_layout.setSpacing(6)

        self.reply_bar = QtWidgets.QFrame()
        self.reply_bar.setObjectName("replyBar")
        self.reply_bar.setVisible(False)
        reply_layout = QtWidgets.QHBoxLayout(self.reply_bar)
        reply_layout.setContentsMargins(10, 6, 10, 6)
        reply_layout.setSpacing(8)

        reply_text = QtWidgets.QVBoxLayout()
        reply_text.setContentsMargins(0, 0, 0, 0)
        reply_text.setSpacing(2)

        self.reply_meta_label = QtWidgets.QLabel("")
        self.reply_meta_label.setObjectName("replyMeta")
        self.reply_body_label = QtWidgets.QLabel("")
        self.reply_body_label.setObjectName("replyBody")
        self.reply_body_label.setWordWrap(True)
        reply_text.addWidget(self.reply_meta_label)
        reply_text.addWidget(self.reply_body_label)

        self.reply_cancel_btn = QtWidgets.QPushButton("×")
        self.reply_cancel_btn.setObjectName("ghostButton")
        self.reply_cancel_btn.setFixedSize(26, 26)
        self.reply_cancel_btn.clicked.connect(self._clear_reply)

        reply_layout.addLayout(reply_text, 1)
        reply_layout.addWidget(self.reply_cancel_btn, 0, QtCore.Qt.AlignTop)

        input_row = QtWidgets.QWidget()
        input_row_layout = QtWidgets.QHBoxLayout(input_row)
        input_row_layout.setContentsMargins(0, 0, 0, 0)
        input_row_layout.setSpacing(8)

        self.input_wrap = QtWidgets.QFrame()
        self.input_wrap.setObjectName("inputWrap")
        input_wrap_layout = QtWidgets.QHBoxLayout(self.input_wrap)
        input_wrap_layout.setContentsMargins(10, 4, 6, 4)
        input_wrap_layout.setSpacing(6)

        self.message_input = MessageInput()
        self.message_input.setObjectName("inputArea")
        self.message_input.setPlaceholderText("Напишите сообщение…")
        self.message_input.send_requested.connect(self.send_message)

        self.attach_btn = QtWidgets.QPushButton("📎")
        self.attach_btn.setObjectName("attachButton")
        self.attach_btn.clicked.connect(self._choose_file)
        self.attach_btn.setFixedSize(32, 32)
        self.attach_btn.setToolTip("Прикрепить файл")

        self.send_btn = QtWidgets.QPushButton("Отправить")
        self.send_btn.setObjectName("primaryButton")
        self.send_btn.clicked.connect(self.send_message)

        input_wrap_layout.addWidget(self.message_input, 1)
        input_wrap_layout.addWidget(self.attach_btn, 0, QtCore.Qt.AlignBottom)

        input_row_layout.addWidget(self.input_wrap, 1)
        input_row_layout.addWidget(self.send_btn)

        bottom_layout.addWidget(self.reply_bar)
        bottom_layout.addWidget(input_row)
        layout.addWidget(bottom)

    def _labeled(self, text, widget):
        wrapper = QtWidgets.QWidget()
        layout = QtWidgets.QHBoxLayout(wrapper)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        label = QtWidgets.QLabel(text)
        label.setObjectName("mutedLabel")
        label.setFixedWidth(90)
        layout.addWidget(label)
        layout.addWidget(widget, 1)
        return wrapper

    def show_login_screen(self, status_text=""):
        self.status_label.setText(status_text)
        if self.pending_socket:
            try:
                self.pending_socket.close()
            except Exception:
                pass
            self.pending_socket = None
        self._reset_pending_code = ""
        self._reset_pending_username = ""
        self._reset_pending_email = ""
        self.stack.setCurrentWidget(self.login_page)
        self.message_input.clear()
        self._clear_reply()
        self._clear_messages()
        self.chat_mode = "group"
        self.dm_target = None
        self.conversations = {"group": []}
        self.message_index = {}
        self.unread_counts = {}
        self.contact_items = []
        self.search_items = []
        self.search_query = ""
        self.latest_users = []
        self.user_directory = {}
        self.pending_contact_adds = set()
        self.users_search.clear()
        with self.upload_states_lock:
            for state in self.upload_states.values():
                try:
                    state.get("cancel").set()
                    dlg = state.get("dialog")
                    if dlg:
                        dlg.close()
                except Exception:
                    pass
            self.upload_states = {}
        with self.download_states_lock:
            for state in self.download_states.values():
                try:
                    worker = state.get("worker")
                    if worker:
                        worker.finish()
                    dlg = state.get("dialog")
                    if dlg:
                        dlg.close()
                except Exception:
                    pass
            self.download_states = {}
        self.downloaded_files = {}
        self.file_button_refs = {}
        for dlg in list(self.profile_dialogs.values()):
            try:
                dlg.close()
            except Exception:
                pass
        self.profile_dialogs = {}

    def show_reset_step1_screen(self):
        self.reset_status_label.setText("")
        self.reset_code_input.clear()
        self.reset_code_row.setVisible(False)
        self.reset_resend_btn.setVisible(False)
        self.reset_send_btn.setEnabled(True)
        self.reset_verify_btn.setEnabled(True)
        self.stack.setCurrentWidget(self.reset_step1_page)

    def show_reset_step2_screen(self):
        self.reset_step2_status.setText("")
        self.reset_new_password_input.clear()
        self.reset_change_btn.setEnabled(True)
        self.stack.setCurrentWidget(self.reset_step2_page)

    def show_chat_screen(self):
        self._update_user_label()
        self.stack.setCurrentWidget(self.chat_page)
        self.set_chat_mode("group")

    def _load_avatar_cache(self):
        try:
            if os.path.exists(self.avatar_cache_path):
                with open(self.avatar_cache_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, dict):
                    return data
        except Exception:
            pass
        return {}

    def _save_avatar_cache(self):
        try:
            with open(self.avatar_cache_path, "w", encoding="utf-8") as f:
                json.dump(self.avatar_cache, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    def _load_saved_tokens(self):
        try:
            if os.path.exists(self.auth_tokens_path):
                with open(self.auth_tokens_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, dict):
                    accounts = data.get("accounts", [])
                    active = (data.get("active") or "").strip()
                    if isinstance(accounts, list):
                        cleaned = []
                        for item in accounts:
                            if not isinstance(item, dict):
                                continue
                            username = (item.get("username") or "").strip()
                            token = (item.get("token") or "").strip()
                            host = (item.get("host") or "").strip() or self.host
                            port = int(item.get("port") or self.port)
                            if not username or not token:
                                continue
                            cleaned.append({
                                "username": username,
                                "token": token,
                                "host": host,
                                "port": port,
                                "last_used": item.get("last_used", 0)
                            })
                        return {"accounts": cleaned, "active": active}
            if os.path.exists(self.auth_token_path):
                with open(self.auth_token_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, dict):
                    token = (data.get("token") or "").strip()
                    username = (data.get("username") or "").strip()
                    if token and username:
                        migrated = {
                            "accounts": [{
                                "username": username,
                                "token": token,
                                "host": self.host,
                                "port": self.port,
                                "last_used": int(time.time())
                            }],
                            "active": username
                        }
                        self._save_tokens(migrated)
                        return migrated
        except Exception:
            pass
        return {"accounts": [], "active": ""}

    def _save_tokens(self, data=None):
        try:
            if data is None:
                data = self.auth_tokens
            with open(self.auth_tokens_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    def _find_saved_account(self, username):
        username = (username or "").strip()
        if not username:
            return None
        for item in self.auth_tokens.get("accounts", []):
            if item.get("username") == username and item.get("host") == self.host and int(item.get("port", 0)) == int(self.port):
                return item
        return None

    def _remember_account(self, username, token, set_active=True):
        username = (username or "").strip()
        token = (token or "").strip()
        if not username or not token:
            return
        accounts = self.auth_tokens.setdefault("accounts", [])
        existing = self._find_saved_account(username)
        now = int(time.time())
        if existing:
            existing["token"] = token
            existing["last_used"] = now
        else:
            accounts.append({
                "username": username,
                "token": token,
                "host": self.host,
                "port": self.port,
                "last_used": now
            })
        if set_active:
            self.auth_tokens["active"] = username
        self._save_tokens()

    def _forget_account(self, username):
        username = (username or "").strip()
        if not username:
            return
        accounts = self.auth_tokens.get("accounts", [])
        self.auth_tokens["accounts"] = [a for a in accounts if not (
            a.get("username") == username and a.get("host") == self.host and int(a.get("port", 0)) == int(self.port)
        )]
        if self.auth_tokens.get("active") == username:
            self.auth_tokens["active"] = ""
        self._save_tokens()

    def _get_active_account(self):
        active = (self.auth_tokens.get("active") or "").strip()
        if active:
            account = self._find_saved_account(active)
            if account:
                return account
        accounts = [a for a in self.auth_tokens.get("accounts", []) if a.get("host") == self.host and int(a.get("port", 0)) == int(self.port)]
        if not accounts:
            return None
        accounts.sort(key=lambda x: int(x.get("last_used", 0)), reverse=True)
        return accounts[0]

    def _clear_saved_token(self):
        try:
            if os.path.exists(self.auth_token_path):
                os.remove(self.auth_token_path)
        except Exception:
            pass

    def _attempt_token_login(self):
        account = self._get_active_account()
        if not account:
            return
        self._attempt_token_login_with(account.get("token"), account.get("username"), "Автовход...")

    def _attempt_token_login_with(self, token, username, status_text=""):
        token = (token or "").strip()
        username = (username or "").strip()
        if not token:
            return
        if username:
            self.username = username
        if status_text:
            self.status_label.setText(status_text)
        self.connect_btn.setEnabled(False)
        self.auth_worker = AuthWorker(
            self.host,
            self.port,
            "",
            "",
            "",
            "token_login",
            token=token
        )

        def on_success(sock, resp, rest):
            self.connect_btn.setEnabled(True)
            if resp.get("status") != "ok":
                try:
                    sock.close()
                except Exception:
                    pass
                self.status_label.setText("")
                if username:
                    self._forget_account(username)
                return

            resp_username = (resp.get("username") or "").strip()
            if resp_username:
                self.username = resp_username

            new_token = (resp.get("token") or "").strip()
            if new_token:
                self._remember_account(self.username, new_token, set_active=True)

            self.client_socket = sock
            self.prebuffer = rest
            self.running = True
            self.show_chat_screen()

            self.sender_thread = SenderThread(self.client_socket)
            self.sender_thread.error.connect(lambda _err: None)
            self.sender_thread.start()
            self._request_profile(self.username)

            self.receiver_thread = ReceiverThread(self.client_socket, self.prebuffer)
            self.receiver_thread.message_received.connect(self.display_message)
            self.receiver_thread.connection_lost.connect(self.connection_lost)
            self.receiver_thread.start()

        def on_error(_message):
            self.connect_btn.setEnabled(True)
            self.status_label.setText("")
            if username:
                self._forget_account(username)

        self.auth_worker.success.connect(on_success)
        self.auth_worker.error.connect(on_error)
        self.auth_worker.start()

    def _send_action(self, payload):
        if not self.sender_thread:
            return
        try:
            self.sender_thread.send(payload)
        except Exception:
            pass

    def _display_name(self, username):
        info = self.user_directory.get(username, {})
        nick = (info.get("nick") or "").strip()
        return nick if nick else username

    def _avatar_data(self, username):
        info = self.user_directory.get(username, {})
        return info.get("avatar", "")

    def _update_user_label(self):
        if not self.username:
            self.user_label.setText("")
            self.user_meta_label.setText("")
            self.self_avatar.setText("")
            self.self_avatar.set_avatar_data("")
            self.user_clan_primary.setVisible(False)
            self.user_clan_dot.setVisible(False)
            self.user_clan_secondary.setVisible(False)
            return
        display = self._display_name(self.username)
        base = display if display else self.username
        self.user_label.setText(base)
        self.user_meta_label.setText(f"@{self.username}")
        self._update_clan_badges(self.my_clan_primary, self.my_clan_secondary)
        initials = base[:1].upper() if base else "?"
        self.self_avatar.setText(initials)
        avatar = self._avatar_data(self.username) or self.avatar_cache.get(self.username, "")
        self.self_avatar.set_avatar_data(avatar)

    def _is_admin(self):
        return (self.my_clan_primary or "").strip() == "Одмен"

    def _update_clan_badges(self, clan_primary, clan_secondary):
        primary = (clan_primary or "").strip()
        secondary = (clan_secondary or "").strip()
        if primary:
            self.user_clan_primary.setText(primary)
            self.user_clan_primary.setVisible(True)
        else:
            self.user_clan_primary.setVisible(False)
        if secondary:
            self.user_clan_secondary.setText(secondary)
            self.user_clan_secondary.setVisible(True)
        else:
            self.user_clan_secondary.setVisible(False)
        self.user_clan_dot.setVisible(bool(primary and secondary))

    def _list_saved_accounts(self):
        accounts = [
            a for a in self.auth_tokens.get("accounts", [])
            if a.get("host") == self.host and int(a.get("port", 0)) == int(self.port)
        ]
        accounts.sort(key=lambda x: int(x.get("last_used", 0)), reverse=True)
        return accounts

    def _show_accounts_menu(self):
        menu = QtWidgets.QMenu(self)
        accounts = self._list_saved_accounts()

        if not accounts:
            empty = QtWidgets.QAction("Нет сохраненных аккаунтов", self)
            empty.setEnabled(False)
            menu.addAction(empty)
        else:
            for item in accounts:
                username = item.get("username")
                if not username:
                    continue
                is_current = username == self.username
                label = f"{username} (текущий)" if is_current else username
                action = QtWidgets.QAction(label, self)
                action.setEnabled(not is_current)
                action.triggered.connect(lambda _=None, u=username: self._switch_account(u))
                menu.addAction(action)

        menu.addSeparator()

        add_action = QtWidgets.QAction("Добавить аккаунт…", self)
        add_action.triggered.connect(self._add_account_flow)
        menu.addAction(add_action)

        if self.username:
            remove_action = QtWidgets.QAction("Удалить текущий аккаунт", self)
            remove_action.triggered.connect(lambda _=None, u=self.username: self._forget_account(u))
            menu.addAction(remove_action)

        pos = self.accounts_btn.mapToGlobal(QtCore.QPoint(0, self.accounts_btn.height()))
        menu.exec_(pos)

    def _add_account_flow(self):
        self._disconnect_internal(show_login=False)
        self.show_login_screen("Войдите в новый аккаунт")
        self.remember_checkbox.setChecked(True)

    def _switch_account(self, username):
        account = self._find_saved_account(username)
        if not account:
            return
        self._disconnect_internal(show_login=False)
        self.show_login_screen("Переключение аккаунта...")
        self._attempt_token_login_with(account.get("token"), account.get("username"), "Переключение аккаунта...")

    def _pixmap_to_data_url(self, pixmap):
        if pixmap is None or pixmap.isNull():
            return ""
        max_side = 256
        if pixmap.width() > max_side or pixmap.height() > max_side:
            pixmap = pixmap.scaled(
                max_side,
                max_side,
                QtCore.Qt.KeepAspectRatio,
                QtCore.Qt.SmoothTransformation
            )
        byte_array = QtCore.QByteArray()
        buffer = QtCore.QBuffer(byte_array)
        buffer.open(QtCore.QIODevice.WriteOnly)
        pixmap.save(buffer, "PNG")
        b64 = base64.b64encode(byte_array.data()).decode("ascii")
        return f"data:image/png;base64,{b64}"

    def _request_profile(self, username):
        self._send_action({"action": "get_profile", "username": username})

    def _request_note(self, username):
        self._send_action({"action": "get_note", "username": username})

    def _save_profile(self, dialog):
        data = dialog.collect_profile_data()
        payload = {"action": "update_profile"}
        payload.update(data)
        self._send_action(payload)
        self._request_profile(dialog.target_username)

    def _save_note(self, dialog):
        note = dialog.collect_note()
        self._send_action({
            "action": "set_note",
            "username": dialog.target_username,
            "note": note
        })

    def _assign_clan(self, dialog):
        clan = dialog.selected_clan()
        if not clan:
            return
        level = dialog.selected_clan_level()
        self._send_action({
            "action": "set_clan",
            "username": dialog.target_username,
            "clan": clan,
            "level": level
        })
        self._request_profile(dialog.target_username)

    def _start_avatar_worker(self, dialog, file_path):
        worker = AvatarEncodeWorker(file_path)
        self._avatar_worker = worker

        def on_done(data_url):
            if dialog.change_avatar_btn:
                dialog.change_avatar_btn.setEnabled(True)
            dialog.set_avatar_data(data_url)
            dialog._update_title()

        def on_fail(message):
            if dialog.change_avatar_btn:
                dialog.change_avatar_btn.setEnabled(True)
            QtWidgets.QMessageBox.warning(self, "Ошибка", message)

        worker.completed.connect(on_done)
        worker.failed.connect(on_fail)
        worker.finished.connect(lambda: setattr(self, "_avatar_worker", None))
        worker.start()

    def _choose_avatar(self, dialog):
        file_path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self,
            "Выберите изображение",
            "",
            "Images (*.png *.jpg *.jpeg *.bmp *.gif)"
        )
        if not file_path:
            return
        dialog.change_avatar_btn.setEnabled(False) if dialog.change_avatar_btn else None
        self._start_avatar_worker(dialog, file_path)

    # ================= CONNECT =================

    def _sync_auth_mode(self):
        is_register = self.register_radio.isChecked()
        self.auth_mode = "register" if is_register else "login"
        self.connect_btn.setText("Зарегистрироваться" if is_register else "Войти")
        self.email_row.setVisible(is_register)
        self.code_row.setVisible(False)

    def connect_to_server(self):
        self.username = self.username_input.text().strip()
        self.password = self.password_input.text().strip()
        self.email = self.email_input.text().strip()
        self.status_label.setText("")

        if not self.username:
            QtWidgets.QMessageBox.warning(self, "Ошибка", "Введите логин")
            return
        if not self.password:
            QtWidgets.QMessageBox.warning(self, "Ошибка", "Введите пароль")
            return
        if self.auth_mode == "register" and not self.email:
            QtWidgets.QMessageBox.warning(self, "Ошибка", "Введите почту")
            return

        if not re.match(r'^[A-Za-z0-9_]+$', self.username):
            QtWidgets.QMessageBox.warning(self, "Ошибка", "Допустимы латиница, цифры и _")
            return

        self.connect_btn.setEnabled(False)
        self.status_label.setText("Подключение...")

        self.auth_worker = AuthWorker(
            self.host,
            self.port,
            self.username,
            self.password,
            self.email,
            self.auth_mode
        )

        def on_success(sock, resp, rest_or_status):
            self.connect_btn.setEnabled(True)
            if resp.get("status") == "need_code":
                self.pending_socket = sock
                self.status_label.setText("Мы отправили код на почту")
                self.code_row.setVisible(True)
                self.connect_btn.setEnabled(False)
                return

            resp_username = (resp.get("username") or "").strip()
            if resp_username:
                self.username = resp_username

            self.client_socket = sock
            self.prebuffer = rest_or_status
            self.running = True
            self.show_chat_screen()

            self.sender_thread = SenderThread(self.client_socket)
            self.sender_thread.error.connect(lambda _err: None)
            self.sender_thread.start()
            self._request_profile(self.username)

            self.receiver_thread = ReceiverThread(self.client_socket, self.prebuffer)
            self.receiver_thread.message_received.connect(self.display_message)
            self.receiver_thread.connection_lost.connect(self.connection_lost)
            self.receiver_thread.start()

            token = resp.get("token", "")
            if token and self.remember_checkbox.isChecked():
                self._remember_account(self.username, token, set_active=True)

        def on_error(message):
            self.connect_btn.setEnabled(True)
            self.status_label.setText("")
            QtWidgets.QMessageBox.critical(self, "Ошибка", message)

        self.auth_worker.success.connect(on_success)
        self.auth_worker.error.connect(on_error)
        self.auth_worker.start()

    def _request_password_reset(self):
        username = self.reset_username_input.text().strip()
        email = self.reset_email_input.text().strip()
        if not username:
            QtWidgets.QMessageBox.warning(self, "Ошибка", "Введите логин")
            return
        if not email:
            QtWidgets.QMessageBox.warning(self, "Ошибка", "Введите почту")
            return
        if not re.match(r'^[A-Za-z0-9_]+$', username):
            QtWidgets.QMessageBox.warning(self, "Ошибка", "Допустимы латиница, цифры и _")
            return

        self.reset_send_btn.setEnabled(False)
        self.reset_resend_btn.setEnabled(False)
        self.reset_status_label.setText("Отправка кода...")

        self.auth_worker = AuthWorker(
            self.host,
            self.port,
            username,
            "",
            email,
            "reset_request"
        )

        def on_success(sock, resp, rest_or_status):
            if resp.get("status") == "need_code":
                self.pending_socket = sock
                self.reset_status_label.setText("Мы отправили код на почту")
                self.reset_code_row.setVisible(True)
                self.reset_resend_btn.setVisible(True)
                self.reset_send_btn.setEnabled(True)
                self.reset_resend_btn.setEnabled(True)
                self._reset_pending_username = username
                self._reset_pending_email = email
                return
            try:
                sock.close()
            except Exception:
                pass
            self.reset_send_btn.setEnabled(True)
            self.reset_resend_btn.setEnabled(True)

        def on_error(message):
            self.reset_send_btn.setEnabled(True)
            self.reset_resend_btn.setEnabled(True)
            self.reset_status_label.setText("")
            QtWidgets.QMessageBox.critical(self, "Ошибка", message)

        self.auth_worker.success.connect(on_success)
        self.auth_worker.error.connect(on_error)
        self.auth_worker.start()

    def _resend_reset_code(self):
        self._request_password_reset()

    def verify_code(self):
        code = self.code_input.text().strip()
        if not code:
            self.status_label.setText("Введите код")
            return

        try:
            if not self.pending_socket:
                self.status_label.setText("Нет ожидающей проверки")
                return

            data = json.dumps({
                "action": "verify",
                "username": self.username,
                "code": code
            }) + "\n"

            self.pending_socket.send(data.encode())

            buffer = ""
            while "\n" not in buffer:
                chunk = self.pending_socket.recv(1024).decode()
                if not chunk:
                    raise Exception("Server closed")
                buffer += chunk

            line, _ = buffer.split("\n", 1)
            resp = json.loads(line)

            if resp.get("status") == "ok":
                try:
                    self.pending_socket.close()
                except Exception:
                    pass
                self.pending_socket = None
                self.connect_btn.setEnabled(True)
                self.show_login_screen("Готово! Можно входить.")
                return

            msg = resp.get("message", "Код не подошёл")
            self.status_label.setText(msg)

        except Exception as e:
            self.status_label.setText(str(e))

    def _verify_reset_code_step1(self):
        code = self.reset_code_input.text().strip()
        if not code:
            self.reset_status_label.setText("Введите код")
            return

        try:
            if not self.pending_socket:
                self.reset_status_label.setText("Нет ожидающей проверки")
                return

            username = self.reset_username_input.text().strip()
            data = json.dumps({
                "action": "reset_check",
                "username": username,
                "code": code
            }) + "\n"

            self.pending_socket.send(data.encode())

            buffer = ""
            while "\n" not in buffer:
                chunk = self.pending_socket.recv(1024).decode()
                if not chunk:
                    raise Exception("Server closed")
                buffer += chunk

            line, _ = buffer.split("\n", 1)
            resp = json.loads(line)

            if resp.get("status") == "ok":
                self._reset_pending_code = code
                self.show_reset_step2_screen()
                return

            msg = resp.get("message", "Код не подошёл")
            self.reset_status_label.setText(msg)

        except Exception as e:
            self.reset_status_label.setText(str(e))

    def _verify_reset_code_step2(self):
        new_password = self.reset_new_password_input.text().strip()
        if not new_password:
            self.reset_step2_status.setText("Введите новый пароль")
            return
        if not getattr(self, "_reset_pending_code", ""):
            self.reset_step2_status.setText("Сначала подтвердите код")
            return

        try:
            if not self.pending_socket:
                self.reset_step2_status.setText("Нет ожидающей проверки")
                return

            username = self.reset_username_input.text().strip()
            data = json.dumps({
                "action": "reset_verify",
                "username": username,
                "code": self._reset_pending_code,
                "password": new_password
            }) + "\n"

            self.pending_socket.send(data.encode())

            buffer = ""
            while "\n" not in buffer:
                chunk = self.pending_socket.recv(1024).decode()
                if not chunk:
                    raise Exception("Server closed")
                buffer += chunk

            line, _ = buffer.split("\n", 1)
            resp = json.loads(line)

            if resp.get("status") == "ok":
                try:
                    self.pending_socket.close()
                except Exception:
                    pass
                self.pending_socket = None
                self._reset_pending_code = ""
                self.show_login_screen("Пароль обновлен. Можно входить.")
                return

            msg = resp.get("message", "Код не подошёл")
            self.reset_step2_status.setText(msg)

        except Exception as e:
            self.reset_step2_status.setText(str(e))

    # ================= SEND =================

    def send_message(self):
        msg = self.message_input.toPlainText().strip()
        if not msg:
            return

        try:
            payload = {"message": msg}
            if self.reply_to_id:
                payload["reply_to"] = self.reply_to_id
            if self.chat_mode == "dm" and self.dm_target:
                payload["to"] = self.dm_target
            data = json.dumps(payload) + "\n"
            self._send_action(payload)
            self.message_input.clear()
            self._clear_reply()
        except Exception:
            QtWidgets.QMessageBox.critical(self, "Ошибка", "Не удалось отправить")

    def _choose_file(self):
        file_path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self,
            "Выберите файл",
            "",
            "Все файлы (*.*)"
        )
        if not file_path:
            return
        self._start_file_upload(file_path)

    def _start_file_upload(self, file_path):
        try:
            size = os.path.getsize(file_path)
        except Exception:
            QtWidgets.QMessageBox.critical(self, "Ошибка", "Не удалось прочитать файл")
            return
        if size <= 0:
            QtWidgets.QMessageBox.information(self, "Файл", "Файл пустой")
            return
        if not self.sender_thread:
            return

        file_id = secrets.token_hex(12)
        filename = os.path.basename(file_path)
        target = self.dm_target if self.chat_mode == "dm" and self.dm_target else ""

        ready_event = threading.Event()
        cancel_event = threading.Event()

        progress = QtWidgets.QProgressDialog(self)
        progress.setWindowTitle("Загрузка файла")
        progress.setLabelText(f"Отправка: {filename}")
        progress.setCancelButtonText("Отмена")
        progress.setMinimumDuration(0)
        progress.setRange(0, size)
        progress.setValue(0)

        def _on_cancel():
            cancel_event.set()
            self._send_action({"action": "file_upload_cancel", "file_id": file_id})
            progress.close()

        progress.canceled.connect(_on_cancel)

        status_ref = {"allowed": False, "error": ""}
        state = {
            "event": ready_event,
            "cancel": cancel_event,
            "dialog": progress,
            "path": file_path,
            "status": status_ref
        }
        with self.upload_states_lock:
            self.upload_states[file_id] = state

        worker = FileUploadWorker(
            file_id=file_id,
            file_path=file_path,
            total_size=size,
            send_func=self._send_action,
            ready_event=ready_event,
            cancel_event=cancel_event,
            status_ref=status_ref
        )
        state["worker"] = worker

        worker.progress.connect(lambda sent, total, d=progress: d.setValue(sent))
        worker.finished.connect(lambda _fid=file_id: self._finish_upload(_fid))
        worker.error.connect(lambda msg, _fid=file_id: self._fail_upload(_fid, msg))
        worker.start()

        self._send_action({
            "action": "file_upload_start",
            "file_id": file_id,
            "filename": filename,
            "size": size,
            "to": target,
            "reply_to": self.reply_to_id
        })
        self._clear_reply()

    def _finish_upload(self, file_id):
        with self.upload_states_lock:
            state = self.upload_states.pop(file_id, None)
        if not state:
            return
        dialog = state.get("dialog")
        if dialog:
            dialog.setValue(dialog.maximum())
            dialog.close()

    def _fail_upload(self, file_id, message):
        with self.upload_states_lock:
            state = self.upload_states.pop(file_id, None)
        if state:
            dialog = state.get("dialog")
            if dialog:
                dialog.close()
        if message:
            QtWidgets.QMessageBox.critical(self, "Ошибка", message)

    def _handle_upload_ready(self, data):
        file_id = (data.get("file_id") or "").strip()
        if not file_id:
            return
        with self.upload_states_lock:
            state = self.upload_states.get(file_id)
        if state:
            state["status"]["allowed"] = True
            state["event"].set()

    def _handle_upload_reject(self, data):
        file_id = (data.get("file_id") or "").strip()
        message = data.get("message", "Загрузка отклонена")
        if not file_id:
            return
        with self.upload_states_lock:
            state = self.upload_states.get(file_id)
        if state:
            state["cancel"].set()
            state["status"]["allowed"] = False
            state["status"]["error"] = message
            state["event"].set()
        if state and state["cancel"].is_set() and message == "Canceled":
            self._finish_upload(file_id)
            return
        self._fail_upload(file_id, message)

    def _handle_upload_done(self, data):
        file_id = (data.get("file_id") or "").strip()
        if not file_id:
            return
        self._finish_upload(file_id)

    def _format_bytes(self, size):
        try:
            size = float(size)
        except Exception:
            return ""
        units = ["Б", "КБ", "МБ", "ГБ"]
        idx = 0
        while size >= 1024 and idx < len(units) - 1:
            size /= 1024.0
            idx += 1
        if idx == 0:
            return f"{int(size)} {units[idx]}"
        return f"{size:.1f} {units[idx]}"

    def _make_download_path(self, filename):
        safe_name = filename or "file"
        base_dir = self.downloads_dir
        os.makedirs(base_dir, exist_ok=True)
        name, ext = os.path.splitext(safe_name)
        candidate = os.path.join(base_dir, safe_name)
        counter = 1
        while os.path.exists(candidate):
            candidate = os.path.join(base_dir, f"{name} ({counter}){ext}")
            counter += 1
        return candidate

    def _open_downloaded_file(self, path):
        if not path:
            return
        if not os.path.exists(path):
            QtWidgets.QMessageBox.critical(self, "Ошибка", "Файл не найден")
            return
        try:
            os.startfile(path)
        except Exception:
            QtWidgets.QMessageBox.critical(self, "Ошибка", "Не удалось открыть файл")

    def _set_file_button_download(self, button, file_id, filename, size):
        if not button:
            return
        try:
            button.clicked.disconnect()
        except Exception:
            pass
        button.setText("Скачать")
        button.setEnabled(bool(file_id))
        button.clicked.connect(
            lambda _=None, fid=file_id, fn=filename, sz=size: self._start_file_download(fid, fn, sz)
        )

    def _set_file_button_open(self, button, path):
        if not button:
            return
        try:
            button.clicked.disconnect()
        except Exception:
            pass
        button.setText("Открыть")
        button.setEnabled(True)
        button.clicked.connect(lambda _=None, p=path: self._open_downloaded_file(p))

    def _register_file_button(self, file_id, button, filename, size):
        if not file_id:
            self._set_file_button_download(button, file_id, filename, size)
            return
        refs = self.file_button_refs.get(file_id)
        if refs is None:
            self.file_button_refs[file_id] = [button]
        else:
            refs.append(button)
        if file_id in self.downloaded_files:
            self._set_file_button_open(button, self.downloaded_files[file_id])
        else:
            self._set_file_button_download(button, file_id, filename, size)

    def _update_file_buttons_open(self, file_id, path):
        refs = self.file_button_refs.get(file_id, [])
        if not refs:
            return
        stale = []
        for btn in refs:
            try:
                self._set_file_button_open(btn, path)
            except RuntimeError:
                stale.append(btn)
        if stale:
            self.file_button_refs[file_id] = [b for b in refs if b not in stale]

    def _start_file_download(self, file_id, filename, size):
        if not file_id:
            return
        with self.download_states_lock:
            if file_id in self.download_states:
                return
            dialog = QtWidgets.QProgressDialog(self)
            dialog.setWindowTitle("Загрузка файла")
            dialog.setLabelText(f"Скачивание: {filename}")
            dialog.setCancelButtonText("Отмена")
            dialog.setMinimumDuration(0)
            dialog.setRange(0, int(size) if int(size or 0) > 0 else 0)
            dialog.setValue(0)
            state = {"dialog": dialog, "worker": None, "filename": filename, "size": int(size or 0)}
            self.download_states[file_id] = state

        dialog.canceled.connect(lambda _fid=file_id: self._cancel_download(_fid))
        self._send_action({"action": "file_request", "file_id": file_id})

    def _cancel_download(self, file_id):
        with self.download_states_lock:
            state = self.download_states.pop(file_id, None)
        if not state:
            return
        worker = state.get("worker")
        if worker:
            worker.finish()
        dialog = state.get("dialog")
        if dialog:
            dialog.close()

    def _handle_file_info(self, data):
        file_id = (data.get("file_id") or "").strip()
        if not file_id:
            return
        filename = data.get("filename") or "file"
        size = int(data.get("size") or 0)

        with self.download_states_lock:
            state = self.download_states.get(file_id)
        if not state:
            self._start_file_download(file_id, filename, size)
            with self.download_states_lock:
                state = self.download_states.get(file_id)
        if not state:
            return
        if state.get("worker"):
            return

        dialog = state.get("dialog")
        if dialog:
            dialog.setLabelText(f"Скачивание: {filename}")
            dialog.setRange(0, size if size > 0 else 0)

        target_path = self._make_download_path(filename)
        worker = FileDownloadWorker(file_id, target_path, size)
        state["worker"] = worker
        state["path"] = target_path
        state["size"] = size
        worker.progress.connect(lambda rec, total, d=dialog: d.setValue(rec))
        worker.finished.connect(lambda _fid=file_id: self._finish_download(_fid))
        worker.error.connect(lambda msg, _fid=file_id: self._fail_download(_fid, msg))
        worker.start()

    def _handle_file_chunk(self, data):
        file_id = (data.get("file_id") or "").strip()
        if not file_id:
            return
        chunk_b64 = data.get("data")
        if not chunk_b64:
            return
        with self.download_states_lock:
            state = self.download_states.get(file_id)
        if not state or not state.get("worker"):
            return
        try:
            raw = base64.b64decode(chunk_b64)
        except Exception:
            return
        state["worker"].add_chunk(raw)

    def _handle_file_end(self, data):
        file_id = (data.get("file_id") or "").strip()
        ok = bool(data.get("ok", True))
        with self.download_states_lock:
            state = self.download_states.get(file_id)
        if not state:
            return
        worker = state.get("worker")
        if worker:
            worker.finish()
        if not ok:
            self._fail_download(file_id, "Загрузка завершилась ошибкой")

    def _finish_download(self, file_id):
        with self.download_states_lock:
            state = self.download_states.pop(file_id, None)
        if not state:
            return
        dialog = state.get("dialog")
        if dialog:
            dialog.setValue(dialog.maximum())
            dialog.close()
        path = state.get("path")
        if path:
            self.downloaded_files[file_id] = path
            self._update_file_buttons_open(file_id, path)

    def _fail_download(self, file_id, message):
        with self.download_states_lock:
            state = self.download_states.pop(file_id, None)
        if state:
            dialog = state.get("dialog")
            if dialog:
                dialog.close()
        if message:
            QtWidgets.QMessageBox.critical(self, "Ошибка", message)

    def _clear_reply(self):
        self.reply_to_id = None
        if getattr(self, "reply_bar", None):
            self.reply_bar.setVisible(False)
        if getattr(self, "reply_meta_label", None):
            self.reply_meta_label.setText("")
        if getattr(self, "reply_body_label", None):
            self.reply_body_label.setText("")

    def _clip_text(self, text, limit=120):
        text = (text or "").replace("\n", " ").strip()
        if len(text) <= limit:
            return text
        return text[: max(0, limit - 1)].rstrip() + "…"

    def _message_preview_text(self, data):
        if not isinstance(data, dict):
            return ""
        if data.get("type") == "file":
            name = data.get("filename") or "file"
            return f"Файл: {name}"
        return data.get("message", "")

    def _start_reply(self, data):
        if not isinstance(data, dict):
            return
        msg_id = data.get("id")
        if not msg_id:
            return
        sender = data.get("username", "")
        if sender == self.username:
            display = "You"
        else:
            display = self._display_name(sender)
        self.reply_to_id = msg_id
        snippet = self._clip_text(self._message_preview_text(data), 160)
        self.reply_meta_label.setText(f"Ответ на {display}")
        self.reply_body_label.setText(snippet if snippet else "Без текста")
        self.reply_bar.setVisible(True)
        self.message_input.setFocus()

    def _find_message_by_id(self, msg_id, scope_items=None):
        if not msg_id:
            return None
        cached = self.message_index.get(msg_id)
        if cached:
            return cached
        if scope_items is None:
            scope_items = []
        for item in scope_items:
            if not isinstance(item, dict):
                continue
            if item.get("id") == msg_id:
                return item
        return None

    def _reply_preview_info(self, data):
        reply_id = data.get("reply_to")
        if not reply_id:
            return None
        if self.chat_mode == "group":
            items = self.conversations.get("group", [])
        else:
            items = self.conversations.get(f"dm:{self.dm_target}", [])
        original = self._find_message_by_id(reply_id, items)
        if not original:
            return {
                "title": "Ответ",
                "body": "Сообщение недоступно"
            }
        sender = original.get("username", "")
        if sender == self.username:
            display = "You"
        else:
            display = self._display_name(sender)
        return {
            "title": f"Ответ на {display}",
            "body": self._clip_text(self._message_preview_text(original), 140) or "Без текста"
        }

    def _show_message_menu(self, widget, pos, data):
        menu = QtWidgets.QMenu(self)
        reply_action = QtWidgets.QAction("Ответить", self)
        reply_action.setEnabled(bool(data.get("id")))
        reply_action.triggered.connect(lambda _=None, d=data: self._start_reply(d))
        menu.addAction(reply_action)
        menu.exec_(widget.mapToGlobal(pos))

    def _bind_message_actions(self, widget, data):
        widget.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)
        widget.customContextMenuRequested.connect(
            lambda pos, w=widget, d=data: self._show_message_menu(w, pos, d)
        )

        def _on_double_click(event, d=data):
            if d.get("id"):
                self._start_reply(d)

        widget.mouseDoubleClickEvent = _on_double_click

    # ================= DISPLAY =================

    def display_message(self, data):
        if data.get("type") == "history":
            items = data.get("items", [])
            for item in items:
                if isinstance(item, dict):
                    self.display_message(item)
            return
        if data.get("type") == "session":
            username = (data.get("username") or "").strip()
            if username and username != self.username:
                self.username = username
                self._update_user_label()
                if self.chat_mode == "group" or self.chat_mode == "dm":
                    self._render_conversation()
            return
        if data.get("type") == "dm_history":
            items = data.get("items", [])
            for item in items:
                if isinstance(item, dict):
                    self._store_message(item)
            self._rebuild_unread_counts()
            if self.latest_users:
                self._render_users_list()
            if self.chat_mode == "dm":
                self._render_conversation()
                self._send_read_receipts_for_current()
            return
        if data.get("type") == "dm_receipt":
            self._apply_receipt(data)
            return
        if data.get("type") == "contacts":
            self._update_contacts(data.get("contacts", []))
            return
        if data.get("type") == "search_results":
            self._apply_search_results(data)
            return
        if data.get("type") == "profile":
            username = (data.get("username") or "").strip()
            profile = data.get("profile") or {}
            if username:
                entry = self.user_directory.setdefault(username, {})
                if isinstance(profile, dict):
                    entry["nick"] = profile.get("nick", "")
                    entry["avatar"] = profile.get("avatar", "")
                    entry["clan_primary"] = profile.get("clan_primary", "")
                    entry["clan_secondary"] = profile.get("clan_secondary", "")
                    avatar = profile.get("avatar", "")
                    if avatar:
                        self.avatar_cache[username] = avatar
                        self._save_avatar_cache()
            dialog = self.profile_dialogs.get(username)
            if dialog and isinstance(profile, dict):
                dialog.set_profile(profile)
            if username == self.username:
                new_primary = (profile.get("clan_primary") or "").strip() if isinstance(profile, dict) else ""
                new_secondary = (profile.get("clan_secondary") or "").strip() if isinstance(profile, dict) else ""
                if new_primary != self.my_clan_primary or new_secondary != self.my_clan_secondary:
                    self.my_clan_primary = new_primary
                    self.my_clan_secondary = new_secondary
                    for dlg in list(self.profile_dialogs.values()):
                        dlg.set_admin(self._is_admin())
                self._update_user_label()
            if self.latest_users:
                self._render_users_list()
            return
        if data.get("type") == "note":
            username = (data.get("username") or "").strip()
            dialog = self.profile_dialogs.get(username)
            if dialog:
                dialog.set_note(data.get("note", ""))
            return
        if data.get("type") == "file_upload_ready":
            self._handle_upload_ready(data)
            return
        if data.get("type") == "file_upload_reject":
            self._handle_upload_reject(data)
            return
        if data.get("type") == "file_upload_done":
            self._handle_upload_done(data)
            return
        if data.get("type") == "file_info":
            self._handle_file_info(data)
            return
        if data.get("type") == "file_chunk":
            self._handle_file_chunk(data)
            return
        if data.get("type") == "file_end":
            self._handle_file_end(data)
            return
        if data.get("type") == "file_error":
            self._fail_download((data.get("file_id") or "").strip(), data.get("message", "Ошибка загрузки"))
            return
        self._store_message(data)
        if self._should_render(data):
            self._render_message(data)
            if data.get("type") == "dm":
                self._maybe_send_read_receipt(data)
        else:
            if data.get("type") == "dm":
                self._maybe_increment_unread(data)
        return

    def _render_message(self, data):

        user = data.get("username", "")
        text = data.get("message", "")
        time = data.get("timestamp", "")
        tp = data.get("type", "message")

        msg_widget = QtWidgets.QFrame()
        msg_layout = QtWidgets.QVBoxLayout(msg_widget)
        msg_layout.setContentsMargins(10, 8, 10, 8)
        msg_layout.setSpacing(4)

        if tp == "system":
            msg_widget.setObjectName("messageSystem")
            label = QtWidgets.QLabel(text)
            label.setAlignment(QtCore.Qt.AlignCenter)
            label.setWordWrap(True)
            msg_layout.addWidget(label)
            alignment = QtCore.Qt.AlignHCenter
            msg_type = "system"
        else:
            is_me = user == self.username
            msg_widget.setObjectName("messageMe" if is_me else "messageOther")
            reply_info = self._reply_preview_info(data)
            if reply_info:
                reply_frame = QtWidgets.QFrame()
                reply_frame.setObjectName("replyPreview")
                reply_layout = QtWidgets.QVBoxLayout(reply_frame)
                reply_layout.setContentsMargins(8, 6, 8, 6)
                reply_layout.setSpacing(2)

                reply_title = QtWidgets.QLabel(reply_info["title"])
                reply_title.setObjectName("replyMeta")
                reply_body = QtWidgets.QLabel(reply_info["body"])
                reply_body.setObjectName("replyBody")
                reply_body.setWordWrap(True)

                reply_layout.addWidget(reply_title)
                reply_layout.addWidget(reply_body)
                msg_layout.addWidget(reply_frame)

            is_dm = tp == "dm" or (tp == "file" and data.get("to"))
            if is_dm:
                if is_me:
                    target_name = self._display_name(data.get("to", ""))
                    name = f"You -> {target_name}".strip()
                else:
                    sender_name = self._display_name(user)
                    name = f"{sender_name} -> You"
            else:
                name = "You" if is_me else self._display_name(user)
            meta_text = f"{name} • {time}".strip()
            if is_dm and is_me and ("delivered" in data or "read" in data):
                status = self._dm_status_text(data)
                if status:
                    meta_text = f"{meta_text} • {status}".strip()
            meta = QtWidgets.QLabel(meta_text)
            meta.setObjectName("mutedLabel")
            msg_layout.addWidget(meta)
            if tp == "file":
                file_name = data.get("filename") or "file"
                size_val = data.get("size")
                size_text = self._format_bytes(size_val) if size_val is not None else ""
                file_label = QtWidgets.QLabel(f"{file_name} {f'({size_text})' if size_text else ''}".strip())
                file_label.setWordWrap(True)
                download_btn = QtWidgets.QPushButton("Скачать")
                download_btn.setObjectName("ghostButton")
                file_id = data.get("file_id")
                download_btn.setEnabled(bool(file_id))
                download_btn.clicked.connect(
                    lambda _=None, fid=file_id, fn=file_name, sz=size_val: self._start_file_download(fid, fn, sz)
                )
                file_row = QtWidgets.QHBoxLayout()
                file_row.setSpacing(6)
                file_row.addWidget(file_label, 1)
                file_row.addWidget(download_btn, 0, QtCore.Qt.AlignRight)
                msg_layout.addLayout(file_row)
            else:
                body = QtWidgets.QLabel(text)
                body.setWordWrap(True)
                msg_layout.addWidget(body)
            alignment = QtCore.Qt.AlignRight if is_me else QtCore.Qt.AlignLeft
            msg_type = "chat"

            self._bind_message_actions(msg_widget, data)

        msg_widget.setSizePolicy(
            QtWidgets.QSizePolicy.Expanding,
            QtWidgets.QSizePolicy.Preferred
        )
        min_w, max_w = self._message_widths(msg_type)
        msg_widget.setMinimumWidth(min_w)
        msg_widget.setMaximumWidth(max_w)

        row = QtWidgets.QWidget()
        row_layout = QtWidgets.QHBoxLayout(row)
        row_layout.setContentsMargins(0, 0, 0, 0)
        row_layout.setSpacing(0)
        if alignment == QtCore.Qt.AlignRight:
            row_layout.addStretch(1)
            row_layout.addWidget(msg_widget)
        elif alignment == QtCore.Qt.AlignLeft:
            row_layout.addWidget(msg_widget)
            row_layout.addStretch(1)
        else:
            row_layout.addStretch(1)
            row_layout.addWidget(msg_widget)
            row_layout.addStretch(1)

        self.messages_layout.insertWidget(self.messages_layout.count() - 1, row)
        self.messages_widget.adjustSize()
        QtCore.QTimer.singleShot(0, self._scroll_to_bottom)
        QtCore.QTimer.singleShot(50, self._scroll_to_bottom)

    def _conversation_key_for_dm(self, data):
        sender = data.get("username", "")
        target = data.get("to", "")
        if sender == self.username:
            other = target
        else:
            other = sender
        return f"dm:{other}" if other else None

    def _store_message(self, data):
        tp = data.get("type", "message")
        if tp == "dm" or (tp == "file" and data.get("to")):
            msg_id = data.get("id")
            if msg_id:
                existing = self.message_index.get(msg_id)
                if existing:
                    existing.update(data)
                    return
            key = self._conversation_key_for_dm(data)
            if not key:
                return
            msg_copy = dict(data)
            self.conversations.setdefault(key, []).append(msg_copy)
            if msg_id:
                self.message_index[msg_id] = msg_copy
            return
        msg_id = data.get("id")
        self.conversations["group"].append(data)
        if msg_id:
            self.message_index[msg_id] = data

    def _should_render(self, data):
        tp = data.get("type", "message")
        if tp == "dm" or (tp == "file" and data.get("to")):
            if self.chat_mode != "dm" or not self.dm_target:
                return False
            return self._conversation_key_for_dm(data) == f"dm:{self.dm_target}"
        return self.chat_mode == "group"

    def _render_conversation(self):
        self._clear_messages()
        if self.chat_mode == "group":
            items = self.conversations.get("group", [])
        else:
            items = self.conversations.get(f"dm:{self.dm_target}", [])
        for item in items:
            self._render_message(item)

    def set_chat_mode(self, mode, target=None):
        if mode not in ("group", "dm"):
            return
        self._clear_reply()
        self.chat_mode = mode
        self.dm_target = target if mode == "dm" else None
        if mode == "group":
            self.chat_label.setText("Общий чат")
            self.back_btn.setVisible(False)
            self.users_list.clearSelection()
        else:
            display = self._display_name(self.dm_target)
            if display and display != self.dm_target:
                self.chat_label.setText(f"Direct: {display} (@{self.dm_target})")
            else:
                self.chat_label.setText(f"Direct: {self.dm_target}")
            self.back_btn.setVisible(True)
            self._select_user_in_list(self.dm_target)
        self._render_conversation()
        if self.chat_mode == "dm":
            self._clear_unread(self.dm_target)
            self._send_read_receipts_for_current()

    def open_direct_chat(self, item):
        if not item:
            return
        target = (item.data(QtCore.Qt.UserRole) or "").strip()
        if not target or target == self.username:
            return
        self.set_chat_mode("dm", target)

    def back_to_group(self):
        self.set_chat_mode("group")

    def show_user_menu(self, pos):
        item = self.users_list.itemAt(pos)
        if not item:
            return
        self.users_list.setCurrentItem(item)
        username = (item.data(QtCore.Qt.UserRole) or "").strip()
        if not username:
            return

        menu = QtWidgets.QMenu(self)

        direct_action = QtWidgets.QAction("Личное сообщение", self)
        direct_action.setEnabled(username != self.username)
        direct_action.triggered.connect(lambda: self.set_chat_mode("dm", username))
        menu.addAction(direct_action)

        profile_action = QtWidgets.QAction("Профиль", self)
        profile_action.triggered.connect(lambda: self.show_profile(username))
        menu.addAction(profile_action)

        in_contacts = {item.get("username") for item in self.contact_items if item.get("username")}
        remove_action = QtWidgets.QAction("Удалить из контактов", self)
        remove_action.setEnabled(username != self.username and username in in_contacts)
        remove_action.triggered.connect(lambda: self._remove_contact(username))
        menu.addAction(remove_action)

        menu.exec_(self.users_list.mapToGlobal(pos))

    def show_profile(self, username):
        username = (username or "").strip()
        if not username:
            return
        existing = self.profile_dialogs.get(username)
        if existing:
            existing.raise_()
            existing.activateWindow()
            return

        is_self = username == self.username
        dialog = ProfileDialog(self, self.username, username, is_self, self._is_admin(), self.clans)
        self.profile_dialogs[username] = dialog
        dialog.finished.connect(lambda _=None, u=username: self.profile_dialogs.pop(u, None))

        dialog.nick_input.textChanged.connect(dialog._update_title)
        if dialog.change_avatar_btn:
            dialog.change_avatar_btn.clicked.connect(lambda: self._choose_avatar(dialog))
        dialog.save_note_btn.clicked.connect(lambda: self._save_note(dialog))
        if dialog.save_profile_btn:
            dialog.save_profile_btn.clicked.connect(lambda: self._save_profile(dialog))
        if dialog.set_clan_btn:
            dialog.set_clan_btn.clicked.connect(lambda: self._assign_clan(dialog))

        cached = self.user_directory.get(username)
        if cached:
            dialog.set_profile(cached)
        else:
            dialog._update_title()

        self._request_profile(username)
        self._request_note(username)

        dialog.show()
        dialog.raise_()
        dialog.activateWindow()

    def _dm_status_text(self, data):
        delivered = bool(data.get("delivered", False))
        read = bool(data.get("read", False))
        if read:
            return "✓✓ прочитано"
        if delivered:
            return "✓✓ доставлено"
        return "✓ отправлено"

    def _apply_receipt(self, data):
        msg_id = data.get("id")
        if not msg_id:
            return
        msg = self.message_index.get(msg_id)
        if not msg:
            return
        if "delivered" in data:
            msg["delivered"] = bool(data.get("delivered"))
        if "read" in data:
            msg["read"] = bool(data.get("read"))
        if self.chat_mode == "dm":
            key = self._conversation_key_for_dm(msg)
            if key == f"dm:{self.dm_target}":
                self._render_conversation()

    def _rebuild_unread_counts(self):
        self.unread_counts = {}
        for key, items in self.conversations.items():
            if not key.startswith("dm:"):
                continue
            for item in items:
                if item.get("type") not in ("dm", "file"):
                    continue
                if item.get("to") != self.username:
                    continue
                if item.get("read"):
                    continue
                sender = item.get("username")
                if not sender:
                    continue
                self.unread_counts[sender] = self.unread_counts.get(sender, 0) + 1
        if self.chat_mode == "dm" and self.dm_target:
            self.unread_counts[self.dm_target] = 0

    def _maybe_increment_unread(self, data):
        if data.get("type") not in ("dm", "file"):
            return
        if data.get("to") != self.username:
            return
        sender = data.get("username")
        if not sender or sender == self.username:
            return
        if self.chat_mode == "dm" and self.dm_target == sender:
            return
        if data.get("read"):
            return
        self.unread_counts[sender] = self.unread_counts.get(sender, 0) + 1
        if self.latest_users:
            self._render_users_list()

    def _clear_unread(self, username):
        if not username:
            return
        if self.unread_counts.get(username):
            self.unread_counts[username] = 0
            if self.latest_users:
                self._render_users_list()

    def _send_read_receipt(self, msg_id):
        if not msg_id:
            return
        try:
            payload = {
                "type": "dm_read",
                "id": msg_id
            }
            self.client_socket.send((json.dumps(payload) + "\n").encode())
        except Exception:
            pass

    def _maybe_send_read_receipt(self, data):
        if self.chat_mode != "dm" or self.dm_target != data.get("username"):
            return
        if data.get("type") not in ("dm", "file"):
            return
        msg_id = data.get("id")
        if not msg_id:
            return
        if data.get("read"):
            return
        if data.get("username") == self.username:
            return
        data["read"] = True
        self._send_read_receipt(msg_id)

    def _send_read_receipts_for_current(self):
        if self.chat_mode != "dm" or not self.dm_target:
            return
        items = self.conversations.get(f"dm:{self.dm_target}", [])
        for item in items:
            if item.get("username") == self.username:
                continue
            if item.get("type") not in ("dm", "file"):
                continue
            if item.get("read"):
                continue
            msg_id = item.get("id")
            if not msg_id:
                continue
            item["read"] = True
            self._send_read_receipt(msg_id)

    def _update_contacts(self, users):
        items = []
        for u in users:
            if isinstance(u, dict):
                name = (u.get("username", "") or "").strip()
                active = bool(u.get("active"))
                nick = u.get("nick", "")
                avatar = u.get("avatar", "")
            else:
                name = str(u).strip()
                active = False
                nick = ""
                avatar = ""
            if not name:
                continue
            cached = self.avatar_cache.get(name, "")
            if not avatar and cached:
                avatar = cached
            self.user_directory[name] = {
                "nick": nick,
                "avatar": avatar
            }
            if avatar:
                self.avatar_cache[name] = avatar
            items.append({"username": name, "active": active})

        items.sort(key=lambda x: (not x["active"], self._display_name(x["username"]).casefold()))

        self.contact_items = items
        existing = {item.get("username") for item in self.contact_items if item.get("username")}
        if existing:
            self.pending_contact_adds -= existing
        if not self.search_query:
            self.latest_users = list(self.contact_items)
            self._render_users_list()
        self._update_user_label()
        self._save_avatar_cache()

    def _on_search_text_changed(self, text):
        self.search_query = (text or "").strip()
        if not self.search_query:
            self.search_items = []
            self.latest_users = list(self.contact_items)
            self.users_title.setText("Контакты")
            self._render_users_list()
            return
        self.users_title.setText("Поиск")
        self.search_timer.start()

    def _send_search_request(self):
        query = self.search_query
        if not query:
            return
        self._send_action({"action": "search_users", "query": query})

    def _add_contact(self, username):
        if not username or username == self.username:
            return
        if username in self.pending_contact_adds:
            return
        self.pending_contact_adds.add(username)
        if self.search_query:
            self._render_users_list()
        self._send_action({"action": "add_contact", "username": username})

    def _remove_contact(self, username):
        if not username or username == self.username:
            return
        if username in self.pending_contact_adds:
            self.pending_contact_adds.discard(username)
        if self.search_query:
            self._render_users_list()
        self._send_action({"action": "remove_contact", "username": username})

    def _apply_search_results(self, data):
        query = (data.get("query") or "").strip()
        if not query or query != self.search_query:
            return
        results = data.get("results", [])
        items = []
        for u in results:
            if isinstance(u, dict):
                name = (u.get("username", "") or "").strip()
                active = bool(u.get("active"))
                nick = u.get("nick", "")
                avatar = u.get("avatar", "")
            else:
                name = str(u).strip()
                active = False
                nick = ""
                avatar = ""
            if not name or name == self.username:
                continue
            cached = self.avatar_cache.get(name, "")
            if not avatar and cached:
                avatar = cached
            self.user_directory[name] = {
                "nick": nick,
                "avatar": avatar
            }
            if avatar:
                self.avatar_cache[name] = avatar
            items.append({"username": name, "active": active})
        items.sort(key=lambda x: (not x["active"], self._display_name(x["username"]).casefold()))
        self.search_items = items
        if self.search_query:
            self.latest_users = list(self.search_items)
            self._render_users_list()
        self._save_avatar_cache()

    def _render_users_list(self):
        self.users_list.clear()
        is_search = bool(self.search_query)
        in_contacts = {item.get("username") for item in self.contact_items if item.get("username")}
        if not self.latest_users:
            if is_search:
                self.empty_state_label.setText("Нет результатов поиска")
            else:
                self.empty_state_label.setText("Контактов пока нет")
            self.empty_state_label.setVisible(True)
        else:
            self.empty_state_label.setVisible(False)
        for entry in self.latest_users:
            name = entry.get("username")
            active = bool(entry.get("active"))
            if not name:
                continue
            row_height = 40 if is_search else 36
            item = QtWidgets.QListWidgetItem()
            item.setData(QtCore.Qt.UserRole, name)
            item.setSizeHint(QtCore.QSize(0, row_height))
            self.users_list.addItem(item)

            row = QtWidgets.QWidget(self.users_list)
            row.setObjectName("userRow")
            row_layout = QtWidgets.QHBoxLayout(row)
            row_layout.setContentsMargins(4, 4, 4, 4)
            row_layout.setSpacing(6)
            row_layout.setAlignment(QtCore.Qt.AlignVCenter)
            row.setFixedHeight(row_height)

            avatar = AvatarLabel(row)
            avatar.setObjectName("userAvatar")
            display_name = self._display_name(name)
            initials = display_name[:1].upper() if display_name else "?"
            avatar.setText(initials)
            avatar.setFixedSize(28, 28)
            avatar.setSizePolicy(
                QtWidgets.QSizePolicy.Fixed,
                QtWidgets.QSizePolicy.Fixed
            )
            avatar.set_avatar_data(self._avatar_data(name))
            font = avatar.font()
            font.setPixelSize(12)
            font.setBold(True)
            avatar.setFont(font)
            avatar.setAlignment(QtCore.Qt.AlignCenter)
            row_layout.addWidget(avatar, 0, QtCore.Qt.AlignVCenter)

            name_label = QtWidgets.QLabel(row)
            name_label.setAlignment(QtCore.Qt.AlignVCenter)
            name_label.setFixedHeight(28)
            metrics = QtGui.QFontMetrics(name_label.font())
            elided = metrics.elidedText(display_name, QtCore.Qt.ElideRight, 130)
            name_label.setText(elided)
            name_label.setObjectName("userNameActive" if active else "userNameInactive")
            if active:
                font = name_label.font()
                font.setBold(True)
                name_label.setFont(font)
            row_layout.addWidget(name_label, 1, QtCore.Qt.AlignVCenter)

            count = self.unread_counts.get(name, 0)
            badge = QtWidgets.QLabel(row)
            badge.setText(str(count))
            badge.setObjectName("unreadBadge")
            badge.setFixedSize(20, 20)
            badge.setAlignment(QtCore.Qt.AlignCenter)
            badge.setVisible(count > 0)
            row_layout.addWidget(badge, 0, QtCore.Qt.AlignVCenter | QtCore.Qt.AlignRight)

            if is_search:
                if name in in_contacts:
                    added = QtWidgets.QLabel("Добавлено")
                    added.setObjectName("mutedLabel")
                    row_layout.addWidget(added, 0, QtCore.Qt.AlignVCenter | QtCore.Qt.AlignRight)
                else:
                    add_btn = QtWidgets.QPushButton("Добавить")
                    add_btn.setObjectName("ghostButton")
                    add_btn.setFixedHeight(24)
                    is_pending = name in self.pending_contact_adds
                    if is_pending:
                        add_btn.setText("Добавлено")
                        add_btn.setEnabled(False)
                    add_btn.clicked.connect(lambda _=None, u=name: self._add_contact(u))
                    row_layout.addWidget(add_btn, 0, QtCore.Qt.AlignVCenter | QtCore.Qt.AlignRight)

            self.users_list.setItemWidget(item, row)
        self._sync_user_selection(self.users_list.currentItem(), None)

    def _select_user_in_list(self, username):
        if not username:
            return
        for i in range(self.users_list.count()):
            item = self.users_list.item(i)
            if not item:
                continue
            name = (item.data(QtCore.Qt.UserRole) or "").strip()
            if name == username:
                self.users_list.setCurrentItem(item)
                return

    def _sync_user_selection(self, current, previous):
        for i in range(self.users_list.count()):
            item = self.users_list.item(i)
            if not item:
                continue
            row = self.users_list.itemWidget(item)
            if not row:
                continue
            row.setProperty("selected", item == current)
            row.style().unpolish(row)
            row.style().polish(row)

    def _scroll_to_bottom(self):
        bar = self.scroll_area.verticalScrollBar()
        bar.setValue(bar.maximum())

    def _clear_messages(self):
        count = self.messages_layout.count()
        for i in range(count - 1):
            item = self.messages_layout.takeAt(0)
            widget = item.widget()
            if widget:
                widget.setParent(None)

    def _message_widths(self, msg_type):
        viewport = self.scroll_area.viewport().width()
        if viewport <= 0:
            viewport = 720
        if msg_type == "system":
            max_w = int(viewport * 0.6)
            min_w = min(260, max_w)
        else:
            max_w = int(viewport * 0.55)
            min_w = min(max(220, int(viewport * 0.32)), max_w)
        return min_w, max_w

    def _update_message_widths(self):
        for i in range(self.messages_layout.count() - 1):
            row = self.messages_layout.itemAt(i).widget()
            if not row:
                continue
            for child in row.findChildren(QtWidgets.QFrame):
                msg_type = "system" if child.objectName() == "messageSystem" else "chat"
                min_w, max_w = self._message_widths(msg_type)
                child.setMinimumWidth(min_w)
                child.setMaximumWidth(max_w)

    # ================= DISCONNECT =================

        for label in self.findChildren(AvatarLabel):
            try:
                label._stop_decode_thread()
            except Exception:
                pass
        if getattr(self, "_avatar_worker", None) is not None:
            try:
                if self._avatar_worker.isRunning():
                    self._avatar_worker.wait(2000)
            except Exception:
                pass
            self._avatar_worker = None

    def disconnect(self):
        self._disconnect_internal(show_login=True)

    def connection_lost(self):
        if self.running:
            self.running = False
            QtWidgets.QMessageBox.information(self, "Связь", "Соединение потеряно")
            self.show_login_screen()

    def _disconnect_internal(self, show_login=True):
        self.running = False
        if self.receiver_thread:
            self.receiver_thread.stop()
        if self.sender_thread:
            self.sender_thread.stop()
        try:
            if self.client_socket:
                try:
                    self.client_socket.shutdown(socket.SHUT_RDWR)
                except Exception:
                    pass
                self.client_socket.close()
        except Exception:
            pass
        if self.receiver_thread:
            self.receiver_thread.wait(2000)
            self.receiver_thread = None
        if self.sender_thread:
            self.sender_thread.wait(2000)
            self.sender_thread = None
        if show_login:
            self.show_login_screen()

    def closeEvent(self, event):
        self._disconnect_internal(show_login=False)
        event.accept()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._update_message_widths()


def main():
    app = QtWidgets.QApplication(sys.argv)
    window = MAXutkoClient()
    window.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
