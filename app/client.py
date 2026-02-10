import sys
import os
import socket
import ssl
import hashlib
import threading
import json
import re
import base64
import io
import time
import secrets
import queue
import getpass
import gc
from PyQt5 import QtCore, QtWidgets, QtGui
from overlay_utils import OverlayController
from notifications import WindowsNotifier, PopupNotifier
from PyQt5.QtGui import QIcon

try:
    from cryptography.fernet import Fernet, InvalidToken
    _FERNET_AVAILABLE = True
except Exception:
    Fernet = None
    InvalidToken = None
    _FERNET_AVAILABLE = False

#flag for testing mode
TestingMode = True

VersionBulid = "2.1.2"

# Modify server IP and build version based on TestingMode
if TestingMode:
    SERVER_HOST = "127.0.0.1"
    BUILD_VERSION = f"Build {VersionBulid} - Testing Mode"
else:
    SERVER_HOST = "26.202.145.247"
    BUILD_VERSION = f"Build {VersionBulid}"

SERVER_PORT = 5555
CLANS = ["Одмен", "Новичок", "Старик", "Старейшина", "Модер"]
CLAN_PRIMARY = {"Одмен", "Модер"}
CLAN_SECONDARY = {"Старейшина", "Новичок", "Старик"}

# Update URLs on Google Drive (direct download links)
# TO SET:
# 1. Upload Updates.json to Google Drive
# 2. Upload MAXutko.exe to Google Drive
# 3. Get file IDs from share URLs
# 4. Create direct download links:
#    https://drive.google.com/uc?id=FILE_ID&export=download
UPDATES_JSON_URL = "https://drive.google.com/uc?export=download&id=1DZ6ezC9aR1aJj3xx__uHGR8BgcNBDn8Y"  # Updates.json file ID
UPDATE_EXE_URL = "https://drive.google.com/uc?export=download&id=1dth5XpBctulLFQNpx6im8RavvI_ja1WT"    # MAXutko.exe file ID


def resource_path(relative):
    if hasattr(sys, "_MEIPASS"):
        return os.path.join(sys._MEIPASS, relative)
    return os.path.join(os.path.abspath("."), relative)

TLS_ENABLED = False  # set False to allow plaintext TCP

DEFAULT_SERVER_CERT_RELATIVE = os.path.join("certs", "server_cert.pem")
SERVER_CERT_PATH = os.environ.get("MAXUTKO_SERVER_CERT", resource_path(DEFAULT_SERVER_CERT_RELATIVE))


def _compute_cert_fingerprint(cert_path):
    with open(cert_path, "rb") as f:
        pem_data = f.read()
    try:
        der = ssl.PEM_cert_to_DER_cert(pem_data.decode("ascii"))
    except Exception as e:
        raise ValueError(f"Invalid PEM certificate at {cert_path}: {e}")
    return hashlib.sha256(der).hexdigest()


def _build_tls_client(cert_path):
    if not os.path.exists(cert_path):
        raise FileNotFoundError(f"TLS certificate not found: {cert_path}")
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.minimum_version = ssl.TLSVersion.TLSv1_3
    context.maximum_version = ssl.TLSVersion.TLSv1_3
    context.verify_mode = ssl.CERT_REQUIRED
    context.check_hostname = False  # we pin the certificate instead
    context.options |= ssl.OP_NO_COMPRESSION
    context.load_verify_locations(cafile=cert_path)
    pinned_fingerprint = _compute_cert_fingerprint(cert_path)
    return context, pinned_fingerprint


def create_secure_socket(host, port):
    if not TLS_ENABLED:
        return socket.create_connection((host, port))
    context, pinned_fp = _build_tls_client(SERVER_CERT_PATH)
    raw_sock = socket.create_connection((host, port))
    tls_sock = context.wrap_socket(raw_sock, server_hostname=host)
    peer_cert = tls_sock.getpeercert(binary_form=True)
    if not peer_cert:
        tls_sock.close()
        raise ssl.SSLError("Server did not provide a certificate")
    peer_fp = hashlib.sha256(peer_cert).hexdigest()
    if peer_fp.lower() != pinned_fp.lower():
        tls_sock.close()
        raise ssl.SSLError("Server certificate fingerprint mismatch")
    return tls_sock


class AuthWorker(QtCore.QThread):
    success = QtCore.pyqtSignal(object, dict, str)
    error = QtCore.pyqtSignal(str)

    def __init__(self, host, port, username, password, email, mode, token="", passcode=""):
        super().__init__()
        self.host = host
        self.port = port
        self.username = username
        self.password = password
        self.email = email
        self.mode = mode
        self.token = token
        self.passcode = passcode

    def run(self):
        try:
            sock = create_secure_socket(self.host, self.port)
            login_data = json.dumps({
                "action": self.mode,
                "username": self.username,
                "password": self.password,
                "email": self.email,
                "token": self.token,
                "passcode": self.passcode
            }) + "\n"
            sock.sendall(login_data.encode())

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


class UpdateDownloadWorker(QtCore.QThread):
    progress = QtCore.pyqtSignal(int, int)
    finished = QtCore.pyqtSignal(str, str)  # (file_path, version)
    error = QtCore.pyqtSignal(str)

    def __init__(self, download_url, version, filename="MAXutko.exe"):
        super().__init__()
        self.download_url = download_url
        self.version = version
        self.filename = filename
        self._running = True

    def run(self):
        if not self.download_url:
            self.error.emit("Ссылка скачивания не указана")
            return

        try:
            import urllib.request
            
            # Prepare target path
            downloads_dir = os.path.join(os.path.expanduser("~"), "Downloads")
            os.makedirs(downloads_dir, exist_ok=True)
            target_path = os.path.join(downloads_dir, f"{self.filename}")

            # Download file
            def download_hook(block_num, block_size, total_size):
                if not self._running:
                    raise Exception("Download cancelled")
                downloaded = block_num * block_size
                if total_size > 0:
                    self.progress.emit(downloaded, total_size)

            urllib.request.urlretrieve(self.download_url, target_path, download_hook)

            if self._running:
                self.finished.emit(target_path, self.version)
        except Exception as e:
            self.error.emit(f"Ошибка загрузки: {str(e)}")

    def stop(self):
        self._running = False


class UpdateInfoDownloadWorker(QtCore.QThread):
    """Download and parse Updates.json from Google Drive"""
    finished = QtCore.pyqtSignal(dict)  # {version, changelog}
    error = QtCore.pyqtSignal(str)

    def __init__(self, json_url):
        super().__init__()
        self.json_url = json_url
        self._running = True

    def run(self):
        if not self.json_url:
            self.error.emit("Ссылка на Updates.json не указана")
            return

        try:
            import urllib.request
            
            # Download JSON file
            response = urllib.request.urlopen(self.json_url, timeout=10)
            content = response.read().decode('utf-8')
            data = json.loads(content)
            
            if not self._running:
                return
            
            # Validate data
            update_info = {
                "version": (data.get("version") or "0.0.0").strip(),
                "changelog": (data.get("changelog") or "").strip()
            }
            
            self.finished.emit(update_info)
        except Exception as e:
            self.error.emit(f"Ошибка загрузки обновления: {str(e)}")

    def stop(self):
        self._running = False


class ProfileDialog(QtWidgets.QDialog):
    closed = QtCore.pyqtSignal()

    def __init__(self, parent, owner_username, target_username, is_self, is_admin, clans, embedded=False):
        super().__init__(parent)
        self.owner_username = owner_username
        self.target_username = target_username
        self.is_self = is_self
        self.is_admin = is_admin
        self.clans = list(clans or [])
        self.avatar_data = ""
        self._embedded = bool(embedded)

        self.setWindowTitle("Профиль пользователя")
        if self._embedded:
            self.setWindowFlags(QtCore.Qt.Widget)
        else:
            self.setModal(True)
        self.setMinimumWidth(390)

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)
        layout.setAlignment(QtCore.Qt.AlignTop)

        header = QtWidgets.QHBoxLayout()
        header.setSpacing(10)
        header.setAlignment(QtCore.Qt.AlignTop)

        self.avatar_label = AvatarLabel(self)
        self.avatar_label.setFixedSize(72, 72)
        font = self.avatar_label.font()
        font.setBold(True)
        font.setPixelSize(18)
        self.avatar_label.setFont(font)
        self.avatar_label.setAlignment(QtCore.Qt.AlignCenter)
        header.addWidget(self.avatar_label, 0, QtCore.Qt.AlignTop)

        title_box = QtWidgets.QVBoxLayout()
        title_box.setAlignment(QtCore.Qt.AlignTop)
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
        title_box.addStretch(0)

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
        if self._embedded:
            self.close_btn.setVisible(False)
        else:
            self.close_btn.clicked.connect(self.accept)
        actions.addWidget(self.close_btn)

        layout.addStretch(1)
        layout.addLayout(actions)

    def closeEvent(self, event):
        if self._embedded:
            self.closed.emit()
        super().closeEvent(event)

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


class CreateGroupWidget(QtWidgets.QWidget):
    """Content widget for creating a group - used with OverlayController"""
    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)

        title = QtWidgets.QLabel("Создание группы")
        title.setObjectName("modalTitle")
        title_font = title.font()
        title_font.setPixelSize(14)
        title_font.setBold(True)
        title.setFont(title_font)
        layout.addWidget(title)

        label = QtWidgets.QLabel("Введите название")
        label.setObjectName("mutedLabel")
        layout.addWidget(label)

        self.name_input = QtWidgets.QLineEdit()
        self.name_input.setPlaceholderText("Название группы")
        self.name_input.setObjectName("input")
        layout.addWidget(self.name_input)

        self.private_checkbox = QtWidgets.QCheckBox("Сделать приватной?")
        layout.addWidget(self.private_checkbox)

        layout.addStretch(1)

        btns = QtWidgets.QHBoxLayout()
        btns.addStretch(1)
        self.create_btn = QtWidgets.QPushButton("Создать")
        self.create_btn.setObjectName("primaryButton")
        btns.addWidget(self.create_btn)
        layout.addLayout(btns)

    def group_name(self):
        return (self.name_input.text() or "").strip()

    def is_private(self):
        return bool(self.private_checkbox.isChecked())


class MAXutkoClient(QtWidgets.QMainWindow):

    def __init__(self): 
        super().__init__()

        self.setWindowTitle("MAXutko Messenger")
        self.resize(900, 700)
        self.setMinimumSize(900, 700)
        icon_path = resource_path("app/icon.ico")  # или .png
        self.setWindowIcon(QIcon(icon_path))   


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
        self._passcode_waiting_mode = ""
        self._passcode_verified_mode = ""
        self.chat_mode = "group"
        self.dm_target = None
        self.group_chat_target = None
        self.conversations = {"group": []}
        self.message_index = {}
        self.unread_counts = {}
        self.contact_items = []
        self.search_items = []
        self.search_query = ""
        self.latest_users = []
        self.user_directory = {}
        # Оптимизация отображения сообщений
        self.max_visible_messages = 200  # Максимум сообщений одновременно
        self.batch_render_timer = None
        self.pending_messages_batch = []
        self._scroll_timer_pending = False  # Флаг отложенного скролла
        self._last_rendered_index = {}  # Индексы последнего отрендёренного сообщения по чату
        self._scroll_was_at_bottom = True  # Сохраняет позицию скролла
        self._skip_next_scroll = False  # Флаг для избежания скролла при переключении чатов
        self._last_rendered_count = 0  # Количество отрендёренных сообщений в текущем виде
        self.groups = {}
        self.my_clan_primary = ""
        self.my_clan_secondary = ""
        self.clans = list(CLANS)
        self.profile_dialogs = {}
        self.avatar_cache_path = os.path.join(os.path.dirname(__file__), "avatar_cache.json")
        self.avatar_cache = self._load_avatar_cache()
        self.download_cache_path = os.path.join(os.path.dirname(__file__), "downloads_cache.json")
        self.download_cache = self._load_download_cache()
        self.auth_token_path = os.path.join(os.path.dirname(__file__), "auth_token.json")
        self.auth_tokens_path = os.path.join(os.path.dirname(__file__), "auth_tokens.json")
        self.auth_tokens = self._load_saved_tokens()
        self.settings_path = os.path.join(os.path.dirname(__file__), "settings.json")
        self.settings = self._load_settings()
        self.use_windows_notifications = bool(self.settings.get("windows_notifications", True))
        self.notifier = None
        self.popup_notifier = None
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
        self._pending_delete_msg_id = None
        
        # Updates
        self.updates_overlay = None
        self.current_version = VersionBulid
        self.available_update = None

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
        root_layout.setContentsMargins(0, 0, 0, 0)

        self.login_content = QtWidgets.QWidget()
        content_layout = QtWidgets.QVBoxLayout(self.login_content)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(0)
        content_layout.setAlignment(QtCore.Qt.AlignCenter)

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
        reset_row.addSpacing(8)
        self.passcode_btn = QtWidgets.QPushButton("PssCdHs")
        self.passcode_btn.setObjectName("ghostButton")
        self.passcode_btn.clicked.connect(self._show_passcode_login_overlay)
        reset_row.addWidget(self.passcode_btn, 0, QtCore.Qt.AlignLeft)
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

        content_layout.addWidget(card, 0, QtCore.Qt.AlignCenter)
        root_layout.addStretch(1)
        root_layout.addWidget(self.login_content, 0, QtCore.Qt.AlignHCenter)
        root_layout.addStretch(1)

        build_row = QtWidgets.QWidget()
        build_layout = QtWidgets.QHBoxLayout(build_row)
        build_layout.setContentsMargins(0, 0, 0, 0)
        build_layout.setSpacing(0)
        build_label = QtWidgets.QLabel(BUILD_VERSION)
        build_label.setObjectName("mutedLabel")
        build_layout.addWidget(build_label, 0, QtCore.Qt.AlignLeft | QtCore.Qt.AlignBottom)
        build_layout.addStretch(1)
        root_layout.addWidget(build_row, 0, QtCore.Qt.AlignLeft | QtCore.Qt.AlignBottom)

        self.email_row.setVisible(False)
        self.code_row.setVisible(False)
        self.passcode_login_overlay = OverlayController(
            self.login_page,
            self.login_content,
            modal_size=QtCore.QSize(380, 240)
        )
        self._build_passcode_login_overlay()

    def _build_passcode_login_overlay(self):
        panel = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(panel)
        layout.setContentsMargins(10, 6, 10, 10)
        layout.setSpacing(10)

        title = QtWidgets.QLabel("PassCodeHash")
        title_font = QtGui.QFont("Segoe UI", 12, QtGui.QFont.Bold)
        title.setFont(title_font)
        layout.addWidget(title)

        note = QtWidgets.QLabel("Вставьте PassCodeHash для быстрого входа.")
        note.setWordWrap(True)
        note.setObjectName("mutedLabel")
        layout.addWidget(note)

        self.passcode_login_input = QtWidgets.QLineEdit()
        self.passcode_login_input.setPlaceholderText("PassCodeHash")
        self.passcode_login_input.setObjectName("input")
        self.passcode_login_input.returnPressed.connect(self._passcode_login)
        layout.addWidget(self.passcode_login_input)

        self.passcode_login_btn = QtWidgets.QPushButton("Enter")
        self.passcode_login_btn.setObjectName("primaryButton")
        self.passcode_login_btn.clicked.connect(self._passcode_login)
        layout.addWidget(self.passcode_login_btn)

        self.passcode_login_status = QtWidgets.QLabel("")
        self.passcode_login_status.setObjectName("statusLabel")
        self.passcode_login_status.setAlignment(QtCore.Qt.AlignCenter)
        layout.addWidget(self.passcode_login_status)

        layout.addStretch(1)
        self.passcode_login_overlay.set_content(panel)

    def _show_passcode_login_overlay(self):
        if not getattr(self, "passcode_login_overlay", None):
            return
        self.passcode_login_overlay.resize(self.login_page.rect())
        self.passcode_login_input.clear()
        self.passcode_login_status.setText("")
        self.passcode_login_btn.setEnabled(True)
        self.passcode_login_overlay.show()

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

        self.chat_content = QtWidgets.QWidget()
        content_layout = QtWidgets.QVBoxLayout(self.chat_content)
        content_layout.setSpacing(10)
        content_layout.setContentsMargins(0, 0, 0, 0)

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

        right_box = QtWidgets.QWidget()
        right_layout = QtWidgets.QVBoxLayout(right_box)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(6)

        self.settings_btn = QtWidgets.QPushButton("⚙")
        self.settings_btn.setObjectName("settingsButton")
        self.settings_btn.setFixedSize(32, 32)
        self.settings_btn.setToolTip("Настройки")
        self.settings_btn.clicked.connect(self._show_settings_overlay)
        right_layout.addWidget(self.settings_btn, 0, QtCore.Qt.AlignRight | QtCore.Qt.AlignTop)

        buttons_row = QtWidgets.QWidget()
        buttons_layout = QtWidgets.QHBoxLayout(buttons_row)
        buttons_layout.setContentsMargins(0, 0, 0, 0)
        buttons_layout.setSpacing(8)

        self.accounts_btn = QtWidgets.QPushButton("Аккаунты")
        self.accounts_btn.setObjectName("ghostButton")
        self.accounts_btn.clicked.connect(self._show_accounts_menu)
        buttons_layout.addWidget(self.accounts_btn, 0, QtCore.Qt.AlignRight)

        self.disconnect_btn = QtWidgets.QPushButton("Выйти")
        self.disconnect_btn.setObjectName("dangerButton")
        self.disconnect_btn.clicked.connect(self.disconnect)
        buttons_layout.addWidget(self.disconnect_btn, 0, QtCore.Qt.AlignRight)

        right_layout.addWidget(buttons_row, 0, QtCore.Qt.AlignRight | QtCore.Qt.AlignBottom)
        top_layout.addWidget(right_box)

        content_layout.addWidget(top_bar)

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
        # search + create-group button
        search_row = QtWidgets.QWidget()
        search_row_layout = QtWidgets.QHBoxLayout(search_row)
        search_row_layout.setContentsMargins(0, 0, 0, 0)
        search_row_layout.setSpacing(6)
        search_row_layout.addWidget(self.users_search)
        self.create_group_btn = QtWidgets.QPushButton("+")
        self.create_group_btn.setFixedSize(28, 28)
        self.create_group_btn.setObjectName("ghostButton")
        self.create_group_btn.setToolTip("Создать группу")
        self.create_group_btn.clicked.connect(self._show_create_group_dialog)
        search_row_layout.addWidget(self.create_group_btn)
        users_layout.addWidget(search_row)

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
        self.users_list.itemClicked.connect(self.open_direct_chat)
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
        # Подключаем отслеживание позиции скролла
        self.scroll_area.verticalScrollBar().sliderMoved.connect(self._update_scroll_position)
        self.scroll_area.verticalScrollBar().valueChanged.connect(self._update_scroll_position)

        self.messages_widget = QtWidgets.QWidget()
        self.messages_widget.setObjectName("messagesContainer")
        self.messages_layout = QtWidgets.QVBoxLayout(self.messages_widget)
        self.messages_layout.setContentsMargins(10, 10, 10, 10)
        self.messages_layout.setSpacing(8)
        self.messages_layout.addStretch(1)

        self.scroll_area.setWidget(self.messages_widget)
        center_layout.addWidget(self.scroll_area, 1)
        content_layout.addWidget(center, 1)

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
        content_layout.addWidget(bottom)

        layout.addWidget(self.chat_content, 1)

        self.settings_overlay = OverlayController(self.chat_page, self.chat_content)
        self.profile_overlay = OverlayController(
            self.chat_page,
            self.chat_content,
            modal_size=QtCore.QSize(430, 600)
        )
        self.passcode_overlay = OverlayController(
            self.chat_page,
            self.chat_content,
            modal_size=QtCore.QSize(440, 520)
        )
        self.delete_confirmation_overlay = OverlayController(
            self.chat_page,
            self.chat_content,
            modal_size=QtCore.QSize(350, 210)
        )
        self.create_group_overlay = OverlayController(
            self.chat_page,
            self.chat_content,
            modal_size=QtCore.QSize(340, 230)
        )
        self.group_info_overlay = OverlayController(
            self.chat_page,
            self.chat_content,
            modal_size=QtCore.QSize(360, 450)
        )
        self._build_settings_overlay()
        self._build_passcode_overlay()
        self._build_delete_confirmation_overlay()
        self._build_create_group_overlay()
        self._build_group_info_overlay()
        self._init_notifications()

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

    def _build_settings_overlay(self):
        panel = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(panel)
        layout.setContentsMargins(10, 6, 10, 10)
        layout.setSpacing(12)

        title = QtWidgets.QLabel("Настройки")
        title_font = QtGui.QFont("Segoe UI", 12, QtGui.QFont.Bold)
        title.setFont(title_font)
        layout.addWidget(title)

        self.settings_notifications_checkbox = QtWidgets.QCheckBox("Использовать уведомления Windows?")
        self.settings_notifications_checkbox.setChecked(bool(self.use_windows_notifications))
        self.settings_notifications_checkbox.toggled.connect(self._set_windows_notifications)
        layout.addWidget(self.settings_notifications_checkbox)

        note = QtWidgets.QLabel(
            "Уведомления показываются при новом сообщении в личных сообщениях, когда окно свернуто. "
            "Если Windows не показывает уведомления, выключите переключатель, чтобы использовать встроенные."
        )
        note.setWordWrap(True)
        note.setObjectName("mutedLabel")
        layout.addWidget(note)

        self.passcode_settings_btn = QtWidgets.QPushButton('Настроить "PassCodeHash"')
        self.passcode_settings_btn.setObjectName("ghostButton")
        self.passcode_settings_btn.clicked.connect(self._show_passcode_overlay)
        layout.addWidget(self.passcode_settings_btn)
        layout.addStretch(1)

        if not QtWidgets.QSystemTrayIcon.isSystemTrayAvailable():
            self.use_windows_notifications = False
            self._save_settings()
            self.settings_notifications_checkbox.setChecked(False)
            self.settings_notifications_checkbox.setEnabled(False)
            self.settings_notifications_checkbox.setToolTip("Системные уведомления недоступны.")

        self.settings_overlay.set_content(panel)

    def _build_passcode_overlay(self):
        panel = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(panel)
        layout.setContentsMargins(10, 6, 10, 10)
        layout.setSpacing(10)

        self.passcode_stack = QtWidgets.QStackedWidget()
        layout.addWidget(self.passcode_stack, 1)

        # Home page
        self.passcode_home_page = QtWidgets.QWidget()
        home_layout = QtWidgets.QVBoxLayout(self.passcode_home_page)
        home_layout.setContentsMargins(0, 0, 0, 0)
        home_layout.setSpacing(10)

        title = QtWidgets.QLabel("PassCodeHash")
        title_font = QtGui.QFont("Segoe UI", 12, QtGui.QFont.Bold)
        title.setFont(title_font)
        home_layout.addWidget(title)

        self.passcode_status_label = QtWidgets.QLabel("")
        self.passcode_status_label.setObjectName("mutedLabel")
        home_layout.addWidget(self.passcode_status_label)

        self.passcode_create_btn = QtWidgets.QPushButton("Создать")
        self.passcode_create_btn.setObjectName("primaryButton")
        self.passcode_create_btn.clicked.connect(self._open_passcode_create)
        home_layout.addWidget(self.passcode_create_btn)

        self.passcode_change_btn = QtWidgets.QPushButton("Перегенерировать")
        self.passcode_change_btn.setObjectName("ghostButton")
        self.passcode_change_btn.clicked.connect(self._open_passcode_change)
        home_layout.addWidget(self.passcode_change_btn)

        self.passcode_reset_btn = QtWidgets.QPushButton("Сбросить")
        self.passcode_reset_btn.setObjectName("ghostButton")
        self.passcode_reset_btn.clicked.connect(self._open_passcode_reset)
        home_layout.addWidget(self.passcode_reset_btn)

        home_layout.addStretch(1)
        self.passcode_stack.addWidget(self.passcode_home_page)

        # Create page
        self.passcode_create_page = QtWidgets.QWidget()
        create_layout = QtWidgets.QVBoxLayout(self.passcode_create_page)
        create_layout.setContentsMargins(0, 0, 0, 0)
        create_layout.setSpacing(10)

        create_title = QtWidgets.QLabel("Создать PassCodeHash")
        create_title.setFont(title_font)
        create_layout.addWidget(create_title)

        self.passcode_create_password_input = QtWidgets.QLineEdit()
        self.passcode_create_password_input.setPlaceholderText("Пароль")
        self.passcode_create_password_input.setEchoMode(QtWidgets.QLineEdit.Password)
        self.passcode_create_password_input.setObjectName("input")
        create_layout.addWidget(self._labeled("Password:", self.passcode_create_password_input))

        self.passcode_create_send_btn = QtWidgets.QPushButton("Отправить код")
        self.passcode_create_send_btn.setObjectName("primaryButton")
        self.passcode_create_send_btn.clicked.connect(self._request_passcode_create)
        create_layout.addWidget(self.passcode_create_send_btn)

        self.passcode_create_status = QtWidgets.QLabel("")
        self.passcode_create_status.setObjectName("statusLabel")
        self.passcode_create_status.setAlignment(QtCore.Qt.AlignCenter)
        create_layout.addWidget(self.passcode_create_status)

        self.passcode_create_code_row = QtWidgets.QWidget()
        create_code_layout = QtWidgets.QHBoxLayout(self.passcode_create_code_row)
        create_code_layout.setContentsMargins(0, 0, 0, 0)
        create_code_layout.setSpacing(8)
        self.passcode_create_code_input = QtWidgets.QLineEdit()
        self.passcode_create_code_input.setPlaceholderText("Код из письма")
        self.passcode_create_code_input.setObjectName("input")
        self.passcode_create_verify_btn = QtWidgets.QPushButton("Подтвердить код")
        self.passcode_create_verify_btn.setObjectName("primaryButton")
        self.passcode_create_verify_btn.clicked.connect(self._verify_passcode_create)
        create_code_layout.addWidget(self._labeled("Code:", self.passcode_create_code_input))
        create_code_layout.addWidget(self.passcode_create_verify_btn)
        create_layout.addWidget(self.passcode_create_code_row)

        self.passcode_create_back_btn = QtWidgets.QPushButton("Назад")
        self.passcode_create_back_btn.setObjectName("ghostButton")
        self.passcode_create_back_btn.clicked.connect(self._open_passcode_home)
        create_layout.addWidget(self.passcode_create_back_btn, 0, QtCore.Qt.AlignLeft)

        self.passcode_create_code_row.setVisible(False)
        self.passcode_stack.addWidget(self.passcode_create_page)

        # Change page
        self.passcode_change_page = QtWidgets.QWidget()
        change_layout = QtWidgets.QVBoxLayout(self.passcode_change_page)
        change_layout.setContentsMargins(0, 0, 0, 0)
        change_layout.setSpacing(10)

        change_title = QtWidgets.QLabel("Перегенерировать PassCodeHash")
        change_title.setFont(title_font)
        change_layout.addWidget(change_title)

        self.passcode_change_password_input = QtWidgets.QLineEdit()
        self.passcode_change_password_input.setPlaceholderText("Пароль")
        self.passcode_change_password_input.setEchoMode(QtWidgets.QLineEdit.Password)
        self.passcode_change_password_input.setObjectName("input")
        change_layout.addWidget(self._labeled("Password:", self.passcode_change_password_input))

        self.passcode_change_btn = QtWidgets.QPushButton("Отправить код")
        self.passcode_change_btn.setObjectName("primaryButton")
        self.passcode_change_btn.clicked.connect(self._request_passcode_change)
        change_layout.addWidget(self.passcode_change_btn)

        self.passcode_change_status = QtWidgets.QLabel("")
        self.passcode_change_status.setObjectName("statusLabel")
        self.passcode_change_status.setAlignment(QtCore.Qt.AlignCenter)
        change_layout.addWidget(self.passcode_change_status)

        self.passcode_change_code_row = QtWidgets.QWidget()
        change_code_layout = QtWidgets.QHBoxLayout(self.passcode_change_code_row)
        change_code_layout.setContentsMargins(0, 0, 0, 0)
        change_code_layout.setSpacing(8)
        self.passcode_change_code_input = QtWidgets.QLineEdit()
        self.passcode_change_code_input.setPlaceholderText("Код из письма")
        self.passcode_change_code_input.setObjectName("input")
        self.passcode_change_verify_btn = QtWidgets.QPushButton("Подтвердить код")
        self.passcode_change_verify_btn.setObjectName("primaryButton")
        self.passcode_change_verify_btn.clicked.connect(self._verify_passcode_change)
        change_code_layout.addWidget(self._labeled("Code:", self.passcode_change_code_input))
        change_code_layout.addWidget(self.passcode_change_verify_btn)
        change_layout.addWidget(self.passcode_change_code_row)

        self.passcode_change_back_btn = QtWidgets.QPushButton("Назад")
        self.passcode_change_back_btn.setObjectName("ghostButton")
        self.passcode_change_back_btn.clicked.connect(self._open_passcode_home)
        change_layout.addWidget(self.passcode_change_back_btn, 0, QtCore.Qt.AlignLeft)

        self.passcode_change_code_row.setVisible(False)
        self.passcode_stack.addWidget(self.passcode_change_page)

        # Reset page
        self.passcode_reset_page = QtWidgets.QWidget()
        reset_layout = QtWidgets.QVBoxLayout(self.passcode_reset_page)
        reset_layout.setContentsMargins(0, 0, 0, 0)
        reset_layout.setSpacing(10)

        reset_title = QtWidgets.QLabel("Сбросить PassCodeHash")
        reset_title.setFont(title_font)
        reset_layout.addWidget(reset_title)

        self.passcode_reset_password_input = QtWidgets.QLineEdit()
        self.passcode_reset_password_input.setPlaceholderText("Пароль")
        self.passcode_reset_password_input.setEchoMode(QtWidgets.QLineEdit.Password)
        self.passcode_reset_password_input.setObjectName("input")
        reset_layout.addWidget(self._labeled("Password:", self.passcode_reset_password_input))

        self.passcode_reset_send_btn = QtWidgets.QPushButton("Сбросить")
        self.passcode_reset_send_btn.setObjectName("primaryButton")
        self.passcode_reset_send_btn.clicked.connect(self._request_passcode_reset)
        reset_layout.addWidget(self.passcode_reset_send_btn)

        self.passcode_reset_status = QtWidgets.QLabel("")
        self.passcode_reset_status.setObjectName("statusLabel")
        self.passcode_reset_status.setAlignment(QtCore.Qt.AlignCenter)
        reset_layout.addWidget(self.passcode_reset_status)

        self.passcode_reset_back_btn = QtWidgets.QPushButton("Назад")
        self.passcode_reset_back_btn.setObjectName("ghostButton")
        self.passcode_reset_back_btn.clicked.connect(self._open_passcode_home)
        reset_layout.addWidget(self.passcode_reset_back_btn, 0, QtCore.Qt.AlignLeft)

        self.passcode_stack.addWidget(self.passcode_reset_page)

        # Result page
        self.passcode_result_page = QtWidgets.QWidget()
        result_layout = QtWidgets.QVBoxLayout(self.passcode_result_page)
        result_layout.setContentsMargins(0, 0, 0, 0)
        result_layout.setSpacing(10)

        result_title = QtWidgets.QLabel("PassCodeHash готов")
        result_title.setFont(title_font)
        result_layout.addWidget(result_title)

        warning = QtWidgets.QLabel("Скопируйте этот код и ни в коем случае не делитесь им!!")
        warning.setWordWrap(True)
        warning.setObjectName("statusLabel")
        result_layout.addWidget(warning)

        self.passcode_result_input = QtWidgets.QLineEdit()
        self.passcode_result_input.setObjectName("input")
        self.passcode_result_input.setReadOnly(True)
        result_layout.addWidget(self.passcode_result_input)

        self.passcode_result_copy_btn = QtWidgets.QPushButton("Скопировать")
        self.passcode_result_copy_btn.setObjectName("primaryButton")
        self.passcode_result_copy_btn.clicked.connect(self._copy_passcode_result)
        result_layout.addWidget(self.passcode_result_copy_btn)

        self.passcode_result_status = QtWidgets.QLabel("")
        self.passcode_result_status.setObjectName("statusLabel")
        self.passcode_result_status.setAlignment(QtCore.Qt.AlignCenter)
        result_layout.addWidget(self.passcode_result_status)

        self.passcode_result_back_btn = QtWidgets.QPushButton("Готово")
        self.passcode_result_back_btn.setObjectName("ghostButton")
        self.passcode_result_back_btn.clicked.connect(self._close_passcode_overlay)
        result_layout.addWidget(self.passcode_result_back_btn, 0, QtCore.Qt.AlignLeft)

        result_layout.addStretch(1)
        self.passcode_stack.addWidget(self.passcode_result_page)

        self.passcode_overlay.set_content(panel)

    def _build_delete_confirmation_overlay(self):
        panel = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(panel)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(16)

        title = QtWidgets.QLabel("Удалить сообщение")
        title_font = QtGui.QFont("Segoe UI", 13, QtGui.QFont.Bold)
        title.setFont(title_font)
        title.setAlignment(QtCore.Qt.AlignCenter)
        layout.addWidget(title)

        message = QtWidgets.QLabel("Вы уверены? Это действие необратимо.")
        message.setWordWrap(True)
        message.setAlignment(QtCore.Qt.AlignCenter)
        message.setObjectName("mutedLabel")
        layout.addWidget(message)

        layout.addStretch(1)

        buttons_layout = QtWidgets.QHBoxLayout()
        buttons_layout.setSpacing(10)

        cancel_btn = QtWidgets.QPushButton("Отмена")
        cancel_btn.setObjectName("deleteDialogButton")
        cancel_btn.clicked.connect(self._close_delete_confirmation)
        buttons_layout.addWidget(cancel_btn)

        delete_btn = QtWidgets.QPushButton("Удалить")
        delete_btn.setObjectName("deleteDialogDangerButton")
        delete_btn.clicked.connect(self._confirm_delete_message)
        buttons_layout.addWidget(delete_btn)

        layout.addLayout(buttons_layout)

        self.delete_confirmation_overlay.set_content(panel)

    def _build_create_group_overlay(self):
        self.create_group_widget = CreateGroupWidget()
        self.create_group_widget.create_btn.clicked.connect(self._submit_create_group)
        self.create_group_overlay.set_content(self.create_group_widget)
    
    def _build_group_info_overlay(self):
        panel = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(panel)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        title = QtWidgets.QLabel("О группе")
        title_font = QtGui.QFont("Segoe UI", 13, QtGui.QFont.Bold)
        title.setFont(title_font)
        layout.addWidget(title)

        body = QtWidgets.QWidget()
        body_layout = QtWidgets.QHBoxLayout(body)
        body_layout.setContentsMargins(0, 0, 0, 0)
        body_layout.setSpacing(12)

        left_col = QtWidgets.QWidget()
        left_layout = QtWidgets.QVBoxLayout(left_col)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(8)

        self.group_info_name_label = QtWidgets.QLabel("")
        self.group_info_name_label.setObjectName("groupInfoName")
        self.group_info_name_label.setWordWrap(True)
        left_layout.addWidget(self.group_info_name_label)

        self.group_info_owner_value = QtWidgets.QLabel("—")
        self.group_info_owner_value.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
        left_layout.addWidget(self._labeled("Владелец", self.group_info_owner_value))

        self.group_info_privacy_value = QtWidgets.QLabel("—")
        left_layout.addWidget(self._labeled("Доступ", self.group_info_privacy_value))

        left_layout.addStretch(1)

        right_col = QtWidgets.QFrame()
        right_col.setObjectName("groupInfoMembersPanel")
        right_layout = QtWidgets.QVBoxLayout(right_col)
        right_layout.setContentsMargins(10, 10, 10, 10)
        right_layout.setSpacing(6)

        members_title = QtWidgets.QLabel("Участники")
        members_title.setObjectName("groupInfoMembersTitle")
        right_layout.addWidget(members_title)

        self.group_info_members_list = QtWidgets.QListWidget()
        self.group_info_members_list.setObjectName("groupInfoMembersList")
        self.group_info_members_list.setSelectionMode(QtWidgets.QAbstractItemView.NoSelection)
        self.group_info_members_list.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        right_layout.addWidget(self.group_info_members_list, 1)

        body_layout.addWidget(left_col, 2)
        body_layout.addWidget(right_col, 3)

        layout.addWidget(body, 1)

        close_btn = QtWidgets.QPushButton("Закрыть")
        close_btn.setObjectName("ghostButton")
        close_btn.clicked.connect(self._close_group_info_overlay)
        layout.addWidget(close_btn, 0, QtCore.Qt.AlignRight)

        self.group_info_overlay.set_content(panel)

    def _show_delete_confirmation(self):
        if not getattr(self, "delete_confirmation_overlay", None):
            return
        self.delete_confirmation_overlay.show()

    def _close_delete_confirmation(self):
        if not getattr(self, "delete_confirmation_overlay", None):
            return
        self._pending_delete_msg_id = None
        self.delete_confirmation_overlay.hide()

    def _confirm_delete_message(self):
        msg_id = self._pending_delete_msg_id
        self._pending_delete_msg_id = None
        if getattr(self, "delete_confirmation_overlay", None):
            self.delete_confirmation_overlay.hide()
        
        if not msg_id:
            return
        
        payload = {"delete_message": msg_id}
        self._send_action(payload)

    def _submit_create_group(self):
        name = self.create_group_widget.group_name()
        private = self.create_group_widget.is_private()
        if not name:
            QtWidgets.QMessageBox.warning(self, "Ошибка", "Введите название группы")
            return
        self.create_group_overlay.hide()
        self._send_action({"action": "create_group", "name": name, "private": private})

    def _show_passcode_overlay(self):
        if not getattr(self, "passcode_overlay", None):
            return
        self.profile_overlay.hide()
        # Переходим на главную страницу PassCode
        if getattr(self, "passcode_stack", None):
            self.passcode_stack.setCurrentWidget(self.passcode_home_page)
        self.passcode_overlay.show()
        self._request_passcode_status()

    def _show_create_group_dialog(self):
        if not getattr(self, "create_group_overlay", None):
            return
        self.create_group_widget.name_input.setText("")
        self.create_group_widget.private_checkbox.setChecked(False)
        self.create_group_overlay.show()
    
    def _show_group_info(self, gid):
        if not gid or not getattr(self, "group_info_overlay", None):
            return
        grp = self.groups.get(gid, {})
        name = self._group_display_name(gid)
        self.group_info_name_label.setText(name or "Группа")

        owner = (grp.get("owner") or "").strip() if isinstance(grp, dict) else ""
        if owner:
            self.group_info_owner_value.setText(owner)
        else:
            self.group_info_owner_value.setText("Нет данных")

        private = grp.get("private") if isinstance(grp, dict) else None
        if isinstance(private, bool):
            self.group_info_privacy_value.setText("приватная" if private else "неприватная")
        else:
            self.group_info_privacy_value.setText("Нет данных")

        members = []
        if isinstance(grp, dict):
            members = list(grp.get("members") or [])
        members = [m for m in members if m]
        members.sort(key=lambda x: (x or "").casefold())
        if hasattr(self, "group_info_members_list"):
            self.group_info_members_list.clear()
            if not members:
                empty = QtWidgets.QListWidgetItem("Нет участников")
                empty.setFlags(empty.flags() & ~QtCore.Qt.ItemIsSelectable)
                self.group_info_members_list.addItem(empty)
            else:
                for member in members:
                    display = self._display_name(member) or member
                    item = QtWidgets.QListWidgetItem(display)
                    item.setFlags(item.flags() & ~QtCore.Qt.ItemIsSelectable)
                    self.group_info_members_list.addItem(item)

        self.group_info_overlay.show()

    def _close_group_info_overlay(self):
        if getattr(self, "group_info_overlay", None):
            self.group_info_overlay.hide()

    def _leave_group(self, gid):
        gid = (gid or "").strip()
        if not gid:
            return
        self._apply_group_left_local(gid, silent=True)
        self._send_action({"action": "leave_group", "group_id": gid})

    def _close_passcode_overlay(self):
        if getattr(self, "passcode_overlay", None):
            # Очищаем все поля перед закрытием
            if getattr(self, "passcode_result_input", None):
                self.passcode_result_input.setText("")
            if getattr(self, "passcode_result_status", None):
                self.passcode_result_status.setText("")
            if getattr(self, "passcode_create_password_input", None):
                self.passcode_create_password_input.clear()
            if getattr(self, "passcode_create_code_input", None):
                self.passcode_create_code_input.clear()
            if getattr(self, "passcode_create_status", None):
                self.passcode_create_status.setText("")
            if getattr(self, "passcode_change_password_input", None):
                self.passcode_change_password_input.clear()
            if getattr(self, "passcode_change_code_input", None):
                self.passcode_change_code_input.clear()
            if getattr(self, "passcode_change_status", None):
                self.passcode_change_status.setText("")
            self.passcode_overlay.hide()

    def _open_passcode_home(self):
        if getattr(self, "passcode_stack", None):
            self.passcode_stack.setCurrentWidget(self.passcode_home_page)
        if getattr(self, "passcode_status_label", None):
            self.passcode_status_label.setText("")
        self._passcode_waiting_mode = ""
        self._passcode_verified_mode = ""
        if getattr(self, "passcode_change_btn", None):
            self.passcode_change_btn.setVisible(False)
        if getattr(self, "passcode_reset_btn", None):
            self.passcode_reset_btn.setVisible(False)
        if getattr(self, "passcode_create_btn", None):
            self.passcode_create_btn.setVisible(True)

    def _open_passcode_create(self):
        self._passcode_waiting_mode = ""
        self._passcode_verified_mode = ""
        self.passcode_create_password_input.clear()
        self.passcode_create_code_input.clear()
        self.passcode_create_status.setText("")
        self.passcode_create_code_row.setVisible(False)
        self.passcode_create_send_btn.setEnabled(True)
        self.passcode_create_verify_btn.setEnabled(True)
        self.passcode_stack.setCurrentWidget(self.passcode_create_page)

    def _open_passcode_change(self):
        self._passcode_waiting_mode = ""
        self._passcode_verified_mode = ""
        self.passcode_change_password_input.clear()
        self.passcode_change_code_input.clear()
        self.passcode_change_status.setText("")
        self.passcode_change_code_row.setVisible(False)
        self.passcode_change_btn.setEnabled(True)
        self.passcode_change_verify_btn.setEnabled(True)
        self.passcode_stack.setCurrentWidget(self.passcode_change_page)

    def _open_passcode_reset(self):
        self._passcode_waiting_mode = ""
        self._passcode_verified_mode = ""
        self.passcode_reset_password_input.clear()
        self.passcode_reset_status.setText("")
        self.passcode_reset_send_btn.setEnabled(True)
        self.passcode_stack.setCurrentWidget(self.passcode_reset_page)

    def _request_passcode_status(self):
        if not self.sender_thread:
            return
        self._send_action({"action": "passcode_status"})

    def _request_passcode_create(self):
        password = self.passcode_create_password_input.text().strip()
        if not password:
            self.passcode_create_status.setText("Введите пароль")
            return
        self.passcode_create_send_btn.setEnabled(False)
        self.passcode_create_verify_btn.setEnabled(False)
        self.passcode_create_status.setText("Отправка кода...")
        self._passcode_waiting_mode = "create"
        self._passcode_verified_mode = ""
        self._send_action({
            "action": "passcode_create_request",
            "password": password
        })

    def _verify_passcode_create(self):
        code = self.passcode_create_code_input.text().strip()
        if not code:
            self.passcode_create_status.setText("Введите код")
            return
        self.passcode_create_verify_btn.setEnabled(False)
        self.passcode_create_status.setText("Проверка...")
        self._passcode_verified_mode = "create"
        self._send_action({"action": "passcode_create_verify", "code": code})

    def _request_passcode_change(self):
        password = self.passcode_change_password_input.text().strip()
        if not password:
            self.passcode_change_status.setText("Введите пароль")
            return
        self.passcode_change_btn.setEnabled(False)
        self.passcode_change_verify_btn.setEnabled(False)
        self.passcode_change_status.setText("Отправка кода...")
        self._passcode_waiting_mode = "change"
        self._passcode_verified_mode = ""
        self._send_action({
            "action": "passcode_change",
            "password": password
        })

    def _verify_passcode_change(self):
        code = self.passcode_change_code_input.text().strip()
        if not code:
            self.passcode_change_status.setText("Введите код")
            return
        self.passcode_change_verify_btn.setEnabled(False)
        self.passcode_change_status.setText("Проверка...")
        self._passcode_verified_mode = "change"
        self._send_action({"action": "passcode_change_verify", "code": code})

    def _request_passcode_reset(self):
        password = self.passcode_reset_password_input.text().strip()
        if not password:
            self.passcode_reset_status.setText("Введите пароль")
            return
        self.passcode_reset_send_btn.setEnabled(False)
        self.passcode_reset_status.setText("Сброс...")
        self._passcode_waiting_mode = ""
        self._passcode_verified_mode = ""
        self._send_action({"action": "passcode_reset_request", "password": password})

    def _show_passcode_result(self, passcode):
        self.passcode_result_input.setText(passcode)
        self.passcode_result_status.setText("")
        self.passcode_stack.setCurrentWidget(self.passcode_result_page)

    def _copy_passcode_result(self):
        passcode = self.passcode_result_input.text().strip()
        if not passcode:
            self.passcode_result_status.setText("Нет кода для копирования")
            return
        QtWidgets.QApplication.clipboard().setText(passcode)
        self.passcode_result_status.setText("Скопировано")

    def _handle_passcode_response(self, data):
        status = (data.get("status") or "").strip().lower()
        mode = (data.get("mode") or "").strip().lower()
        message = data.get("message", "")

        if mode == "status":
            has_passcode = bool(data.get("has_passcode", False))
            if getattr(self, "passcode_status_label", None):
                state = "настроен" if has_passcode else "не настроен"
                self.passcode_status_label.setText(f"Статус: {state}")
            if getattr(self, "passcode_change_btn", None):
                self.passcode_change_btn.setVisible(has_passcode)
            if getattr(self, "passcode_reset_btn", None):
                self.passcode_reset_btn.setVisible(has_passcode)
            if getattr(self, "passcode_create_btn", None):
                self.passcode_create_btn.setVisible(not has_passcode)
            return

        if status == "need_code":
            info = message or "Мы отправили код на почту"
            if mode == "create":
                self.passcode_create_status.setText(info)
                self.passcode_create_code_row.setVisible(True)
                self.passcode_create_send_btn.setEnabled(True)
                self.passcode_create_verify_btn.setEnabled(True)
                self._passcode_waiting_mode = "create"
            elif mode == "change":
                self.passcode_change_status.setText(info)
                self.passcode_change_code_row.setVisible(True)
                self.passcode_change_btn.setEnabled(True)
                self.passcode_change_verify_btn.setEnabled(True)
                self._passcode_waiting_mode = "change"
            return

        if status == "ok":
            passcode_value = (data.get("passcode") or "").strip()
            if mode == "create":
                self.passcode_create_send_btn.setEnabled(True)
                self.passcode_create_verify_btn.setEnabled(True)
                if message:
                    self.passcode_create_status.setText(message)
            elif mode == "reset":
                self.passcode_reset_send_btn.setEnabled(True)
                if message:
                    self.passcode_reset_status.setText(message)
            elif mode == "change":
                self.passcode_change_btn.setEnabled(True)
                self.passcode_change_verify_btn.setEnabled(True)
                if message:
                    self.passcode_change_status.setText(message)
            if mode in ("create", "change") and self._passcode_verified_mode != mode:
                if mode == "create":
                    self.passcode_create_status.setText("Подтвердите код из письма")
                elif mode == "change":
                    self.passcode_change_status.setText("Подтвердите код из письма")
                return
            if passcode_value:
                self._passcode_waiting_mode = ""
                self._passcode_verified_mode = ""
                self._show_passcode_result(passcode_value)
            elif mode == "reset":
                self._open_passcode_home()
                self._request_passcode_status()
            return

        if status == "error":
            error_text = message or "Ошибка"
            if mode == "create":
                self.passcode_create_send_btn.setEnabled(True)
                self.passcode_create_verify_btn.setEnabled(True)
                self.passcode_create_status.setText(error_text)
            elif mode == "reset":
                self.passcode_reset_send_btn.setEnabled(True)
                self.passcode_reset_status.setText(error_text)
            elif mode == "change":
                self.passcode_change_btn.setEnabled(True)
                self.passcode_change_verify_btn.setEnabled(True)
                self.passcode_change_status.setText(error_text)
            return

    def _init_notifications(self):
        self.popup_notifier = PopupNotifier(self)
        if not QtWidgets.QSystemTrayIcon.isSystemTrayAvailable():
            self.notifier = None
            return
        self.notifier = WindowsNotifier(self)
        icon = self.windowIcon()
        if icon is None or icon.isNull():
            icon = self.style().standardIcon(QtWidgets.QStyle.SP_ComputerIcon)
        self.notifier.set_icon(icon)
        self.notifier.set_tooltip("MAXutko Messenger")
        self.notifier.message_clicked.connect(self._on_notification_clicked)
        self.popup_notifier.message_clicked.connect(self._on_notification_clicked)
        if self.use_windows_notifications:
            self.notifier.show()

    def _set_windows_notifications(self, enabled):
        self.use_windows_notifications = bool(enabled)
        self._save_settings()
        if self.notifier:
            if self.use_windows_notifications:
                self.notifier.show()
            else:
                self.notifier.hide()

    def _is_window_minimized(self):
        return bool(self.windowState() & QtCore.Qt.WindowMinimized)

    def _notify_dm(self, data):
        if not self._is_window_minimized():
            return
        if data.get("type") not in ("dm", "file"):
            return
        if data.get("to") != self.username:
            return
        sender = data.get("username")
        if not sender or sender == self.username:
            return
        count = self.unread_counts.get(sender, 0)
        if count <= 0:
            count = 1
        title = self._display_name(sender) or sender
        body = f"Новые сообщения ({count})"
        if self.use_windows_notifications and self.notifier:
            self.notifier.notify(title, body, payload={"sender": sender})
        elif self.popup_notifier:
            self.popup_notifier.notify(title, body, payload={"sender": sender})

    def _on_notification_clicked(self, payload):
        sender = (payload or {}).get("sender")
        if not sender:
            return
        self.showNormal()
        self.raise_()
        self.activateWindow()
        self.set_chat_mode("dm", sender)

    def show_login_screen(self, status_text=""):
        self.status_label.setText(status_text)
        if getattr(self, "settings_overlay", None):
            self.settings_overlay.hide()
        if getattr(self, "profile_overlay", None):
            self.profile_overlay.hide()
        if getattr(self, "passcode_overlay", None):
            self.passcode_overlay.hide()
        if getattr(self, "passcode_login_overlay", None):
            self.passcode_login_overlay.hide()
        if getattr(self, "group_info_overlay", None):
            self.group_info_overlay.hide()
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
        if getattr(self, "settings_overlay", None):
            self.settings_overlay.hide()
        if getattr(self, "profile_overlay", None):
            self.profile_overlay.hide()
        if getattr(self, "group_info_overlay", None):
            self.group_info_overlay.hide()

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

    def _load_download_cache(self):
        try:
            if os.path.exists(self.download_cache_path):
                with open(self.download_cache_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, dict):
                    return data
        except Exception:
            pass
        return {}

    def _save_download_cache(self):
        try:
            with open(self.download_cache_path, "w", encoding="utf-8") as f:
                json.dump(self.download_cache, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    def _download_cache_key(self, username=None):
        username = (username or self.username or "").strip()
        if not username:
            return ""
        return f"{username}@{self.host}:{int(self.port)}"

    def _load_downloaded_files_for_account(self, username=None):
        key = self._download_cache_key(username)
        if not key:
            self.downloaded_files = {}
            return
        bucket = self.download_cache.get(key, {})
        if not isinstance(bucket, dict):
            bucket = {}
        cleaned = {}
        for file_id, path in bucket.items():
            if not file_id or not isinstance(path, str):
                continue
            if os.path.exists(path):
                cleaned[file_id] = path
        self.downloaded_files = cleaned
        if cleaned != bucket:
            self.download_cache[key] = cleaned
            self._save_download_cache()

    def _remember_downloaded_file(self, file_id, path):
        file_id = (file_id or "").strip()
        if not file_id or not path:
            return
        key = self._download_cache_key()
        if not key:
            return
        self.downloaded_files[file_id] = path
        bucket = self.download_cache.setdefault(key, {})
        bucket[file_id] = path
        self._save_download_cache()

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
                            # Decrypt token if encrypted
                            token = self._decrypt_token(token)
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
            
            # Encrypt tokens before saving
            data_to_save = dict(data)
            if "accounts" in data_to_save:
                encrypted_accounts = []
                for acc in data_to_save["accounts"]:
                    acc_copy = dict(acc)
                    acc_copy["token"] = self._encrypt_token(acc_copy.get("token", ""))
                    encrypted_accounts.append(acc_copy)
                data_to_save["accounts"] = encrypted_accounts
            
            with open(self.auth_tokens_path, "w", encoding="utf-8") as f:
                json.dump(data_to_save, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    def _encrypt_token(self, token_str):
        """Encrypts token before saving to disk"""
        if not token_str or not _FERNET_AVAILABLE:
            return token_str
        
        try:
            cipher = self._get_token_cipher()
            if cipher is None:
                return token_str
            token_bytes = token_str.encode("utf-8")
            encrypted = cipher.encrypt(token_bytes)
            return encrypted.decode("ascii")
        except Exception:
            return token_str

    def _decrypt_token(self, encrypted_token):
        """Decrypts token when loading from disk"""
        if not encrypted_token or not _FERNET_AVAILABLE:
            return encrypted_token
        
        try:
            cipher = self._get_token_cipher()
            if cipher is None:
                return encrypted_token
            encrypted_bytes = encrypted_token.encode("utf-8")
            decrypted = cipher.decrypt(encrypted_bytes)
            return decrypted.decode("utf-8")
        except (InvalidToken, Exception):
            # Token not encrypted (old format), return as-is
            return encrypted_token

    def _get_token_cipher(self):
        """Gets or creates Fernet cipher for token encryption"""
        if not _FERNET_AVAILABLE:
            return None
        
        try:
            key_path = os.path.join(os.path.dirname(__file__), ".token_key")
            
            if os.path.exists(key_path):
                with open(key_path, "rb") as f:
                    key = f.read()
            else:
                # Generate new key and save it
                key = Fernet.generate_key()
                with open(key_path, "wb") as f:
                    f.write(key)
                try:
                    os.chmod(key_path, 0o600)
                except:
                    pass
            
            return Fernet(key)
        except Exception:
            return None

    def _load_settings(self):
        if not os.path.exists(self.settings_path):
            return {"windows_notifications": True}
        try:
            with open(self.settings_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                return data
        except Exception:
            pass
        return {"windows_notifications": True}

    def _save_settings(self):
        data = {
            "windows_notifications": bool(self.use_windows_notifications)
        }
        try:
            with open(self.settings_path, "w", encoding="utf-8") as f:
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

            self._load_downloaded_files_for_account(self.username)
            self.client_socket = sock
            self.prebuffer = rest
            self.running = True
            self.show_chat_screen()

            # Check for update
            update_info = resp.get("update")
            if update_info:
                self._handle_update_check(update_info)

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

    def _show_profile_overlay(self, dialog):
        if not getattr(self, "profile_overlay", None):
            return
        self.profile_overlay.set_content(dialog)
        self.profile_overlay.show()

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

            self._load_downloaded_files_for_account(self.username)
            self.client_socket = sock
            self.prebuffer = rest_or_status
            self.running = True
            
            # SEC-016: Clear password from memory immediately after auth
            self.password = ""
            self.password_input.clear()
            gc.collect()
            
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
            # SEC-016: Clear password even on error
            self.password = ""
            self.password_input.clear()
            gc.collect()
            
            self.connect_btn.setEnabled(True)
            self.status_label.setText("")
            QtWidgets.QMessageBox.critical(self, "Ошибка", message)

        self.auth_worker.success.connect(on_success)
        self.auth_worker.error.connect(on_error)
        self.auth_worker.start()

    def _passcode_login(self):
        passcode = self.passcode_login_input.text().strip()
        if not passcode:
            self.passcode_login_status.setText("Введите PassCodeHash")
            return

        self.passcode_login_btn.setEnabled(False)
        self.passcode_login_status.setText("Подключение...")

        self.auth_worker = AuthWorker(
            self.host,
            self.port,
            "",
            "",
            "",
            "passcode_login",
            passcode=passcode
        )

        def on_success(sock, resp, rest_or_status):
            self.passcode_login_btn.setEnabled(True)
            resp_username = (resp.get("username") or "").strip()
            if resp_username:
                self.username = resp_username

            self._load_downloaded_files_for_account(self.username)
            self.client_socket = sock
            self.prebuffer = rest_or_status
            self.running = True
            
            # SEC-016: Clear password from memory immediately after auth
            self.password = ""
            self.password_input.clear()
            gc.collect()
            
            if getattr(self, "passcode_login_overlay", None):
                self.passcode_login_overlay.hide()
            self.show_chat_screen()

            self.sender_thread = SenderThread(self.client_socket)
            self.sender_thread.error.connect(lambda _err: None)
            self.sender_thread.start()
            self._request_profile(self.username)

            self.receiver_thread = ReceiverThread(self.client_socket, self.prebuffer)
            self.receiver_thread.message_received.connect(self.display_message)
            self.receiver_thread.connection_lost.connect(self.connection_lost)
            self.receiver_thread.start()

            token = (resp.get("token") or "").strip()
            if token and self.remember_checkbox.isChecked():
                self._remember_account(self.username, token, set_active=True)

        def on_error(message):
            # SEC-016: Clear password even on error
            self.password = ""
            self.password_input.clear()
            gc.collect()
            
            self.passcode_login_btn.setEnabled(True)
            self.passcode_login_status.setText(message)

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
            elif self.chat_mode == "group_chat" and self.group_chat_target:
                payload["action"] = "send_group_message"
                payload["group_id"] = self.group_chat_target
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
        group_id = self.group_chat_target if self.chat_mode == "group_chat" and self.group_chat_target else ""

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
            "group_id": group_id,
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

    def _format_timestamp_hm(self, value):
        if not value:
            return ""
        text = str(value).strip()
        parts = text.split(":")
        if len(parts) >= 2:
            return f"{parts[0]}:{parts[1]}"
        return text

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
            self._remember_downloaded_file(file_id, path)
            self._update_file_buttons_open(file_id, path)
            self._open_downloaded_file(path)

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

    def _handle_message_deleted(self, msg_id):
        cached = self.message_index.get(msg_id)
        chat_key = None
        if isinstance(cached, dict):
            tp = cached.get("type")
            if tp == "dm" or (tp == "file" and cached.get("to")):
                chat_key = self._conversation_key_for_dm(cached)
            elif tp == "group_message" or (tp == "file" and cached.get("group_id")):
                gid = (cached.get("group_id") or "").strip()
                chat_key = f"group:{gid}" if gid else None
            else:
                chat_key = "group"
        for items in self.conversations.values():
            items[:] = [m for m in items if m.get("id") != msg_id]
        if msg_id in self.message_index:
            self.message_index.pop(msg_id, None)
        if chat_key:
            self._last_rendered_index[chat_key] = -1
        else:
            self._last_rendered_index = {}
        self._render_conversation()

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
        elif self.chat_mode == "group_chat" and self.group_chat_target:
            items = self.conversations.get(f"group:{self.group_chat_target}", [])
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
        
        delete_action = QtWidgets.QAction("Удалить", self)
        delete_action.setEnabled(bool(data.get("id") and data.get("username") == self.username))
        delete_action.triggered.connect(lambda _=None, d=data: self._delete_message(d))
        menu.addAction(delete_action)
        
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

    def _delete_message(self, data):
        msg_id = data.get("id")
        if not msg_id or data.get("username") != self.username:
            return
        
        self._pending_delete_msg_id = msg_id
        self._show_delete_confirmation()

    # ================= DISPLAY =================

    def display_message(self, data, _depth=0):
        """
        SEC-005: Prevent infinite recursion with depth limit
        """
        MAX_RECURSION_DEPTH = 5
        if _depth > MAX_RECURSION_DEPTH:
            return
        
        if data.get("type") == "passcode":
            self._handle_passcode_response(data)
            return
        if data.get("type") == "history":
            items = data.get("items", [])
            for item in items:
                if isinstance(item, dict):
                    self.display_message(item, _depth + 1)
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
            # Проверяем есть ли уже кэшированная история
            dm_key = f"dm:{self.dm_target}" if self.dm_target else None
            history_exists = dm_key and dm_key in self.conversations and len(self.conversations[dm_key]) > 0
            
            for item in items:
                if isinstance(item, dict):
                    self._store_message(item)
            self._rebuild_unread_counts()
            if self.latest_users:
                self._render_users_list()
            if self.chat_mode == "dm":
                # Если история уже была загружена, просто рендерим инкрементально
                # Если это первая загрузка - то _render_conversation сама разберёт
                self._render_conversation()
                self._send_read_receipts_for_current()
            return
        if data.get("type") == "dm_receipt":
            self._apply_receipt(data)
            return
        if data.get("type") == "contacts":
            self._update_contacts(data.get("contacts", []))
            return
        if data.get("type") == "user_groups":
            self._load_user_groups(data.get("groups", []))
            return
        if data.get("type") == "group_messages":
            items = data.get("items", [])
            for item in items:
                if isinstance(item, dict):
                    self._store_message(item)
            return
        if data.get("type") == "search_results":
            self._apply_search_results(data)
            return
        if data.get("type") == "group_invite":
            frm = (data.get("from") or "").strip()
            grp = data.get("group") or {}
            gname = grp.get("name", "")
            gid = grp.get("id", "")
            if gid:
                btn = QtWidgets.QMessageBox(self)
                btn.setWindowTitle("Приглашение в группу")
                btn.setText(f"Пользователь {frm} приглашает вас в группу '{gname}'")
                join = btn.addButton("Принять", QtWidgets.QMessageBox.AcceptRole)
                btn.addButton("Отклонить", QtWidgets.QMessageBox.RejectRole)
                btn.exec_()
                if btn.clickedButton() == join:
                    self._send_action({"action": "accept_group_invite", "group_id": gid})
            return
        if data.get("type") == "group_created":
            status = (data.get("status") or "").strip()
            grp = data.get("group") or {}
            gid = (grp.get("id") or "").strip()
            if status == "ok" and gid:
                self.groups[gid] = grp
                # Add to contacts
                self._add_group_to_contacts(gid, grp)
                QtWidgets.QMessageBox.information(self, "Группа создана", f"Группа '{grp.get('name','')}' создана")
            else:
                QtWidgets.QMessageBox.warning(self, "Ошибка", data.get("message", "Ошибка при создании группы"))
            return
        if data.get("type") == "group_joined":
            status = (data.get("status") or "").strip()
            grp = data.get("group") or {}
            gid = (grp.get("id") or "").strip()
            if status == "ok" and gid:
                # remember membership
                self.groups[gid] = grp
                # Add to contacts
                self._add_group_to_contacts(gid, grp)
                QtWidgets.QMessageBox.information(self, "Вступление", f"Вы вступили в группу '{grp.get('name','')}'")
            else:
                QtWidgets.QMessageBox.warning(self, "Ошибка", data.get("message", "Не удалось вступить в группу"))
            return
        if data.get("type") == "group_left":
            status = (data.get("status") or "").strip()
            gid = (data.get("group_id") or "").strip()
            if status == "ok" and gid:
                self._apply_group_left_local(gid, silent=True)
            else:
                QtWidgets.QMessageBox.warning(self, "Ошибка", data.get("message", "Не удалось выйти из группы"))
            return
        if data.get("type") == "group_member_added":
            grp = data.get("group") or {}
            user = (data.get("username") or "").strip()
            if grp and user:
                # Update local group data
                gid = grp.get("id")
                if gid:
                    self.groups[gid] = grp
            return
        if data.get("type") == "group_message":
            self._store_message(data)
            if self.chat_mode == "group_chat" and self.group_chat_target == data.get("group_id"):
                # Просто добавляем новое сообщение, не перестраиваем всё
                self._render_conversation()
            elif self.chat_mode == "group":
                # Если это основной общий чат, показываем сообщение
                self._render_conversation()
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
        if data.get("type") == "message_deleted":
            msg_id = (data.get("id") or "").strip()
            if msg_id:
                self._handle_message_deleted(msg_id)
            return
        self._store_message(data)
        is_dm_item = data.get("type") in ("dm", "file") and data.get("to")
        notified = False
        if is_dm_item and self._is_window_minimized():
            self._maybe_increment_unread(data)
            self._notify_dm(data)
            notified = True
        if self._should_render(data):
            # Если это сообщение в активном DM чате, добавляем инкрементально
            if self.chat_mode == "dm" and self.dm_target:
                self._render_conversation()  # Инкрементальная отрисовка
                if self._scroll_was_at_bottom:
                    QtCore.QTimer.singleShot(5, self._scroll_to_bottom)
            else:
                # Иначе просто отрисовываем сообщение
                self._render_message(data)
            if data.get("type") == "dm":
                self._maybe_send_read_receipt(data)
        else:
            if is_dm_item and not notified:
                self._maybe_increment_unread(data)
                self._notify_dm(data)
        return

    def _render_message(self, data):

        user = data.get("username", "")
        text = data.get("message", "")
        time = data.get("timestamp", "")
        tp = data.get("type", "message")

        is_dm = False
        msg_widget = QtWidgets.QFrame()
        msg_layout = QtWidgets.QVBoxLayout(msg_widget)
        msg_layout.setContentsMargins(10, 8, 10, 8)
        msg_layout.setSpacing(6)

        if tp == "system":
            msg_widget.setObjectName("messageSystem")
            label = QtWidgets.QLabel(text)
            label.setAlignment(QtCore.Qt.AlignCenter)
            label.setWordWrap(True)
            label.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
            msg_layout.addWidget(label)
            alignment = QtCore.Qt.AlignHCenter
            msg_type = "system"
        else:
            is_me = user == self.username
            msg_widget.setObjectName("messageBubbleMe" if is_me else "messageBubbleOther")
            is_dm = tp == "dm" or (tp == "file" and data.get("to"))
            time_text = self._format_timestamp_hm(time)
            display_name = self._display_name(user)
            show_author = True
            reply_info = self._reply_preview_info(data)
            if reply_info:
                reply_frame = QtWidgets.QFrame()
                reply_frame.setObjectName("messageReply")
                reply_layout = QtWidgets.QVBoxLayout(reply_frame)
                reply_layout.setContentsMargins(10, 6, 10, 6)
                reply_layout.setSpacing(2)

                reply_title = QtWidgets.QLabel(reply_info["title"])
                reply_title.setObjectName("messageReplyMeta")
                reply_title.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
                reply_body = QtWidgets.QLabel(reply_info["body"])
                reply_body.setObjectName("messageReplyBody")
                reply_body.setWordWrap(True)
                reply_body.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)

                reply_layout.addWidget(reply_title)
                reply_layout.addWidget(reply_body)
                msg_layout.addWidget(reply_frame)

            if show_author:
                header = QtWidgets.QWidget()
                header.setObjectName("messageHeader")
                header_layout = QtWidgets.QHBoxLayout(header)
                header_layout.setContentsMargins(0, 0, 0, 0)
                header_layout.setSpacing(6)
                author = QtWidgets.QLabel("Вы" if is_me else display_name)
                author.setObjectName("messageAuthor")
                header_layout.addWidget(author, 0, QtCore.Qt.AlignLeft)
                header_layout.addStretch(1)
                time_label = QtWidgets.QLabel(time_text)
                time_label.setObjectName("messageTime")
                header_layout.addWidget(time_label, 0, QtCore.Qt.AlignRight)
                msg_layout.addWidget(header)
            if tp == "file":
                file_name = data.get("filename") or "file"
                size_val = data.get("size")
                size_text = self._format_bytes(size_val) if size_val is not None else ""
                file_card = QtWidgets.QFrame()
                file_card.setObjectName("fileCard")
                file_card_layout = QtWidgets.QVBoxLayout(file_card)
                file_card_layout.setContentsMargins(10, 8, 10, 8)
                file_card_layout.setSpacing(4)
                file_label = QtWidgets.QLabel(file_name)
                file_label.setObjectName("fileName")
                file_label.setWordWrap(True)
                file_label.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
                file_size = QtWidgets.QLabel(size_text)
                file_size.setObjectName("fileSize")
                file_size.setVisible(bool(size_text))
                file_size.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
                file_card_layout.addWidget(file_label)
                file_card_layout.addWidget(file_size)
                file_id = data.get("file_id")
                download_btn = QtWidgets.QPushButton()
                download_btn.setObjectName("fileButton")
                self._register_file_button(file_id, download_btn, file_name, size_val)
                file_row = QtWidgets.QHBoxLayout()
                file_row.setSpacing(8)
                file_row.addWidget(file_card, 1)
                file_row.addWidget(download_btn, 0, QtCore.Qt.AlignRight | QtCore.Qt.AlignTop)
                msg_layout.addLayout(file_row)
            else:
                body = QtWidgets.QLabel(text)
                body.setWordWrap(True)
                body.setObjectName("messageBody")
                body.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
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

        wrapper = QtWidgets.QWidget()
        wrapper_layout = QtWidgets.QVBoxLayout(wrapper)
        wrapper_layout.setContentsMargins(0, 0, 0, 0)
        wrapper_layout.setSpacing(4)
        wrapper_layout.addWidget(msg_widget)
        if tp != "system" and is_dm:
            meta = QtWidgets.QWidget()
            meta.setObjectName("messageMeta")
            meta_layout = QtWidgets.QHBoxLayout(meta)
            meta_layout.setContentsMargins(2, 0, 2, 0)
            meta_layout.setSpacing(4)
            if not show_author:
                meta_time = QtWidgets.QLabel(time_text)
                meta_time.setObjectName("messageMetaTime")
                meta_layout.addWidget(meta_time)
            if is_me and ("delivered" in data or "read" in data):
                status = self._dm_status_text(data)
                if status:
                    meta_status = QtWidgets.QLabel(status)
                    meta_status.setObjectName("messageMetaStatus")
                    meta_layout.addWidget(meta_status)
            if is_me:
                meta_layout.insertStretch(0, 1)
            else:
                meta_layout.addStretch(1)
            wrapper_layout.addWidget(meta)

        row = QtWidgets.QWidget()
        row.setObjectName("messageRow")
        row_layout = QtWidgets.QHBoxLayout(row)
        row_layout.setContentsMargins(0, 0, 0, 0)
        row_layout.setSpacing(8)
        if alignment == QtCore.Qt.AlignRight:
            row_layout.addStretch(1)
            row_layout.addWidget(wrapper)
        elif alignment == QtCore.Qt.AlignLeft:
            avatar = AvatarLabel(row)
            avatar.setObjectName("messageAvatar")
            initials = (self._display_name(user)[:1] or "?").upper()
            avatar.setText(initials)
            avatar.setFixedSize(30, 30)
            avatar_font = avatar.font()
            avatar_font.setBold(True)
            avatar_font.setPixelSize(11)
            avatar.setFont(avatar_font)
            avatar.setAlignment(QtCore.Qt.AlignCenter)
            avatar.set_avatar_data(self._avatar_data(user))
            row_layout.addWidget(avatar, 0, QtCore.Qt.AlignTop)
            row_layout.addWidget(wrapper)
            row_layout.addStretch(1)
        else:
            row_layout.addStretch(1)
            row_layout.addWidget(wrapper)
            row_layout.addStretch(1)

        self.messages_layout.insertWidget(self.messages_layout.count() - 1, row)
        # Сдерживаем скролл - используем общий таймер вместо множественных вызовов
        if not getattr(self, '_scroll_timer_pending', False):
            self._scroll_timer_pending = True
            QtCore.QTimer.singleShot(50, self._deferred_scroll_to_bottom)

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
        if tp == "file" and data.get("group_id"):
            msg_id = data.get("id")
            gid = (data.get("group_id") or "").strip()
            if not gid:
                return
            if msg_id:
                existing = self.message_index.get(msg_id)
                if existing:
                    existing.update(data)
                    return
            key = f"group:{gid}"
            msg_copy = dict(data)
            msg_copy["group_id"] = gid
            self.conversations.setdefault(key, []).append(msg_copy)
            if msg_id:
                self.message_index[msg_id] = msg_copy
            return
        if tp == "group_message":
            msg_id = data.get("id")
            gid = (data.get("group_id") or "").strip()
            if not gid:
                return
            if msg_id:
                existing = self.message_index.get(msg_id)
                if existing:
                    existing.update(data)
                    return
            key = f"group:{gid}"
            msg_copy = dict(data)
            msg_copy["group_id"] = gid
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
        if tp == "file" and data.get("group_id"):
            if self.chat_mode != "group_chat" or not self.group_chat_target:
                return False
            gid = (data.get("group_id") or "").strip()
            return gid == self.group_chat_target
        if tp == "group_message":
            if self.chat_mode != "group_chat" or not self.group_chat_target:
                return False
            gid = (data.get("group_id") or "").strip()
            return gid == self.group_chat_target
        return self.chat_mode == "group"

    def _render_conversation(self):
        """Умная отрисовка с инкрементальным обновлением"""
        # Определяем ключ текущего чата
        if self.chat_mode == "group":
            chat_key = "group"
        elif self.chat_mode == "group_chat" and self.group_chat_target:
            chat_key = f"group:{self.group_chat_target}"
        else:
            chat_key = f"dm:{self.dm_target}"
        
        items = self.conversations.get(chat_key, [])
        
        # Если это первое отрисовка для этого чата - перестраиваем всё
        last_idx = self._last_rendered_index.get(chat_key, -1)
        if last_idx == -1:
            # Первая отрисовка - чистим и отрисовываем всё
            self._clear_messages()
            self._skip_next_scroll = True
            
            # Ограничиваем количество видимых сообщений для производительности
            if len(items) > self.max_visible_messages:
                display_items = items[-self.max_visible_messages:]
                start_idx = len(items) - self.max_visible_messages
            else:
                display_items = items
                start_idx = 0
            
            for idx, item in enumerate(display_items):
                self._render_message(item)
                self._last_rendered_index[chat_key] = start_idx + idx
            
            self._last_rendered_count = len(display_items)
            QtCore.QTimer.singleShot(10, self._scroll_to_bottom)
            self._skip_next_scroll = False
        else:
            # Инкрементальное добавление - только новые сообщения
            if len(items) > last_idx + 1:
                # Есть новые сообщения
                for idx in range(last_idx + 1, len(items)):
                    self._render_message(items[idx])
                    self._last_rendered_index[chat_key] = idx
                
                # Скролл в конец если он был там
                if self._scroll_was_at_bottom:
                    QtCore.QTimer.singleShot(5, self._scroll_to_bottom)

    def _flush_message_batch(self):
        """Отрисовывает накопленные сообщения батчем"""
        if not self.pending_messages_batch:
            self.batch_render_timer = None
            return
        
        # Отрисовываем все сообщения из батча
        for msg in self.pending_messages_batch:
            self._render_message(msg)
        
        # Очищаем батч
        self.pending_messages_batch.clear()
        self.batch_render_timer = None
        # ОднократнΞ готовим скролл в конец
        QtCore.QTimer.singleShot(5, self._scroll_to_bottom)

    def set_chat_mode(self, mode, target=None, group_id=None):
        if mode not in ("group", "dm", "group_chat"):
            return
        # Очищаем батч перед сменой режима чата
        if self.batch_render_timer:
            self.batch_render_timer.stop()
            self.batch_render_timer = None
        if self.pending_messages_batch:
            self._flush_message_batch()
        
        # Сохраняем позицию скролла для текущего чата перед переключением
        self._update_scroll_position()
        
        self._clear_reply()
        self.chat_mode = mode
        self.dm_target = target if mode == "dm" else None
        normalized_group_id = (str(group_id).strip() if group_id is not None else "")
        self.group_chat_target = normalized_group_id if mode == "group_chat" and normalized_group_id else None
        
        if mode == "group":
            self.chat_label.setText("Общий чат")
            self.back_btn.setVisible(False)
            self.users_list.clearSelection()
        elif mode == "group_chat":
            grp = self.groups.get(group_id, {})
            gname = (grp.get("name") or "").strip()
            self.chat_label.setText(f"Группа: {gname}")
            self.back_btn.setVisible(True)
            self._select_user_in_list(None)
        else:
            display = self._display_name(self.dm_target)
            if display and display != self.dm_target:
                self.chat_label.setText(f"Direct: {display} (@{self.dm_target})")
            else:
                self.chat_label.setText(f"Direct: {self.dm_target}")
            self.back_btn.setVisible(True)
            self._select_user_in_list(self.dm_target)
        
        # Проверяем есть ли уже кэшированная история для этого чата
        if mode == "group":
            chat_key = "group"
        elif mode == "group_chat":
            chat_key = f"group:{self.group_chat_target}"
        else:
            chat_key = f"dm:{self.dm_target}"

        # Если переключаемся на другой чат, форсируем полную отрисовку
        if getattr(self, "_current_chat_key", None) != chat_key:
            self._current_chat_key = chat_key
            self._last_rendered_index[chat_key] = -1

        # Если история уже кэширована - используем умный рендер с инкрементом
        # Если её нет - то при рендере будет полная перестройка
        self._render_conversation()
        
        if self.chat_mode == "dm":
            self._clear_unread(self.dm_target)
            self._send_read_receipts_for_current()

    def open_direct_chat(self, item):
        if not item:
            return
        data = item.data(QtCore.Qt.UserRole)
        # Check if it's a group
        if isinstance(data, dict) and data.get("group_id"):
            gid = (str(data.get("group_id")) or "").strip()
            self.set_chat_mode("group_chat", group_id=gid)
            return
        # Regular user
        target = (data or "").strip()
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
        data = item.data(QtCore.Qt.UserRole)
        # Group context menu
        if isinstance(data, dict) and data.get("group_id"):
            self._show_group_menu(pos)
            return
        username = (data or "").strip()
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

        # Invite to group submenu (groups where current user is a member)
        try:
            my_groups = [g for g in self.groups.values() if self.username in (g.get("members") or [])]
        except Exception:
            my_groups = []
        if my_groups:
            invite_menu = QtWidgets.QMenu("Пригласить в группу", self)
            for g in my_groups:
                gid = g.get("id")
                gname = g.get("name")
                if not gid:
                    continue
                act = QtWidgets.QAction(gname or gid, self)
                act.triggered.connect(lambda _=None, _gid=gid, _u=username: self._send_action({"action": "invite_group", "group_id": _gid, "username": _u}))
                invite_menu.addAction(act)
            menu.addMenu(invite_menu)

        menu.exec_(self.users_list.mapToGlobal(pos))

    def _show_group_menu(self, pos):
        item = self.users_list.itemAt(pos)
        if not item:
            return
        self.users_list.setCurrentItem(item)
        data = item.data(QtCore.Qt.UserRole)
        if not isinstance(data, dict):
            return
        gid = (str(data.get("group_id")) or "").strip()
        if not gid:
            return

        menu = QtWidgets.QMenu(self)

        about_action = QtWidgets.QAction("О группе", self)
        about_action.triggered.connect(lambda: self._show_group_info(gid))
        menu.addAction(about_action)

        leave_action = QtWidgets.QAction("Выйти", self)
        grp = self.groups.get(gid, {})
        in_group = False
        try:
            in_group = self.username in (grp.get("members") or [])
        except Exception:
            in_group = False
        leave_action.setEnabled(in_group)
        leave_action.triggered.connect(lambda: self._leave_group(gid))
        menu.addAction(leave_action)

        menu.exec_(self.users_list.mapToGlobal(pos))

    def show_profile(self, username):
        username = (username or "").strip()
        if not username:
            return
        existing = self.profile_dialogs.get(username)
        if existing:
            self._show_profile_overlay(existing)
            return

        is_self = username == self.username
        dialog = ProfileDialog(self, self.username, username, is_self, self._is_admin(), self.clans, embedded=True)
        self.profile_dialogs[username] = dialog

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

        self._show_profile_overlay(dialog)

    def _dm_status_text(self, data):
        delivered = bool(data.get("delivered", False))
        read = bool(data.get("read", False))
        if read:
            return "✓✓"
        if delivered:
            return "✓"
        return ""

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
        if self.chat_mode == "dm" and self.dm_target == sender and not self._is_window_minimized():
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
        if self._is_window_minimized():
            return
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
        if self._is_window_minimized():
            return
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

    def _add_group_to_contacts(self, gid, grp):
        """Add a group to the contacts list"""
        if not gid:
            return
        gname = (grp.get("name") or "").strip()
        if not gname:
            return
        # Check if already in contacts
        existing = [item for item in self.contact_items if item.get("group_id") == gid]
        if existing:
            return
        # Add to contacts list
        self.contact_items.append({"group_id": gid, "group_name": gname, "is_group": True})
        # Refresh display
        if not self.search_query:
            self.latest_users = list(self.contact_items)
            self._render_users_list()

    def _group_display_name(self, gid):
        gid = (gid or "").strip()
        if not gid:
            return ""
        grp = self.groups.get(gid, {})
        name = (grp.get("name") or "").strip() if isinstance(grp, dict) else ""
        if name:
            return name
        for items in (self.contact_items, self.latest_users, self.search_items):
            for item in items:
                if item.get("group_id") == gid:
                    return (item.get("group_name") or "").strip()
        return gid

    def _remove_group_from_contacts(self, gid):
        gid = (gid or "").strip()
        if not gid:
            return
        self.contact_items = [item for item in self.contact_items if item.get("group_id") != gid]
        if not self.search_query:
            self.latest_users = list(self.contact_items)
            self._render_users_list()

    def _apply_group_left_local(self, gid, silent=False):
        gid = (gid or "").strip()
        if not gid:
            return
        gname = self._group_display_name(gid)
        if self.group_chat_target == gid:
            self.set_chat_mode("group")
        self.groups.pop(gid, None)
        self._remove_group_from_contacts(gid)
        if getattr(self, "group_info_overlay", None) and self.group_info_overlay.is_visible():
            self.group_info_overlay.hide()
        if not silent:
            if gname:
                QtWidgets.QMessageBox.information(self, "Группа", f"Вы вышли из группы '{gname}'")
            else:
                QtWidgets.QMessageBox.information(self, "Группа", "Вы вышли из группы")

    def _load_user_groups(self, groups):
        """Load groups that the user is a member of"""
        for grp in groups:
            try:
                gid = (grp.get("id") or "").strip()
                if not gid:
                    continue
                self.groups[gid] = grp
            except Exception:
                continue
        # Refresh contacts display if needed
        if not self.search_query:
            self._update_contacts([])


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

        # Add user's groups to contacts
        for gid, grp in self.groups.items():
            gname = (grp.get("name") or "").strip()
            if gname:
                items.append({"group_id": gid, "group_name": gname, "is_group": True})

        # Sort mixed results (users and groups)
        def _sort_key(x):
            if x.get("is_group"):
                return (True, (x.get("group_name") or "").casefold())
            return (not bool(x.get("active")), self._display_name(x.get("username") or "").casefold())
        items.sort(key=_sort_key)

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
        groups = data.get("groups", [])
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
        # append groups (search-only results)
        for g in groups:
            try:
                gid = (g.get("id") or "").strip()
                gname = (g.get("name") or "").strip()
                if not gid or not gname:
                    continue
                items.append({"group_id": gid, "group_name": gname, "is_group": True})
            except Exception:
                continue
        # Sort mixed results (users and groups)
        def _sort_key(x):
            if x.get("is_group"):
                return (True, (x.get("group_name") or "").casefold())
            return (not bool(x.get("active")), self._display_name(x.get("username") or "").casefold())
        items.sort(key=_sort_key)
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
            if entry.get("is_group"):
                gid = entry.get("group_id")
                gname = entry.get("group_name")
                row_height = 40 if is_search else 36
                item = QtWidgets.QListWidgetItem()
                item.setData(QtCore.Qt.UserRole, {"group_id": gid})
                item.setSizeHint(QtCore.QSize(0, row_height))
                self.users_list.addItem(item)

                row = QtWidgets.QWidget(self.users_list)
                row.setObjectName("groupRow")
                row_layout = QtWidgets.QHBoxLayout(row)
                row_layout.setContentsMargins(4, 4, 4, 4)
                row_layout.setSpacing(6)
                row_layout.setAlignment(QtCore.Qt.AlignVCenter)
                row.setFixedHeight(row_height)
                if not is_search:
                    row.setAttribute(QtCore.Qt.WA_TransparentForMouseEvents, True)

                label = QtWidgets.QLabel(gname)
                label.setObjectName("userName")
                row_layout.addWidget(label, 1)

                if is_search:
                    join_btn = QtWidgets.QPushButton("Вступить")
                    join_btn.setObjectName("ghostButton")
                    join_btn.setFixedSize(80, 26)
                    def _on_join(_gid=gid):
                        self._send_action({"action": "join_group", "group_id": _gid})
                    join_btn.clicked.connect(_on_join)
                    row_layout.addWidget(join_btn)

                self.users_list.setItemWidget(item, row)
                continue

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
            if not is_search:
                row.setAttribute(QtCore.Qt.WA_TransparentForMouseEvents, True)

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
            # Clear selection if no username
            self.users_list.clearSelection()
            return
        for i in range(self.users_list.count()):
            item = self.users_list.item(i)
            if not item:
                continue
            data = item.data(QtCore.Qt.UserRole)
            # Handle string usernames
            if isinstance(data, str):
                name = data.strip()
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
        if not self._skip_next_scroll:
            bar = self.scroll_area.verticalScrollBar()
            bar.setValue(bar.maximum())
            self._scroll_was_at_bottom = True
        else:
            self._skip_next_scroll = False

    def _update_scroll_position(self):
        """Отслеживает позицию скролла (находимся ли мы в конце)"""
        bar = self.scroll_area.verticalScrollBar()
        # Положение "в конце" если мы на 95% вниз или больше
        max_val = bar.maximum()
        curr_val = bar.value()
        self._scroll_was_at_bottom = (max_val - curr_val) <= (max_val * 0.05 + 50) if max_val > 0 else True

    def _deferred_scroll_to_bottom(self):
        """Отложенный скролл - выполняется один раз после нескольких сообщений"""
        self._scroll_timer_pending = False
        self._scroll_to_bottom()

    def _clear_messages(self):
        count = self.messages_layout.count()
        for i in range(count - 1):
            item = self.messages_layout.takeAt(0)
            widget = item.widget()
            if widget:
                widget.setParent(None)

    def _message_widths(self, msg_type):
        viewport = self.scroll_area.viewport().width()
        # Минимум 400px для корректного отображения
        if viewport <= 0:
            viewport = 400
        
        # Используем более агрессивный процент для маленьких окон
        if viewport < 500:
            # На маленьких экранах используем 85-90% viewport
            if msg_type == "system":
                max_w = int(viewport * 0.85)
                min_w = min(200, max_w)
            else:
                max_w = int(viewport * 0.88)
                min_w = min(max(120, int(viewport * 0.70)), max_w)
        else:
            # На больших экранах обычная логика
            if msg_type == "system":
                max_w = int(viewport * 0.55)
                min_w = min(260, max_w)
            else:
                max_w = int(viewport * 0.62)
                min_w = min(max(240, int(viewport * 0.34)), max_w)
        return min_w, max_w

    def _update_message_widths(self):
        for i in range(self.messages_layout.count() - 1):
            row = self.messages_layout.itemAt(i).widget()
            if not row:
                continue
            for child in row.findChildren(QtWidgets.QFrame):
                if child.objectName() not in ("messageSystem", "messageBubbleMe", "messageBubbleOther"):
                    continue
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
        # Очищаем батч перед отключением
        if self.batch_render_timer:
            self.batch_render_timer.stop()
            self.batch_render_timer = None
        self.pending_messages_batch.clear()
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
        if getattr(self, "settings_overlay", None):
            self.settings_overlay.resize(self.chat_page.rect())
        if getattr(self, "passcode_overlay", None):
            self.passcode_overlay.resize(self.chat_page.rect())
        if getattr(self, "passcode_login_overlay", None):
            self.passcode_login_overlay.resize(self.login_page.rect())
        if getattr(self, "group_info_overlay", None):
            self.group_info_overlay.resize(self.chat_page.rect())

    def _show_settings_overlay(self):
        if getattr(self, "settings_overlay", None):
            self.settings_overlay.show()

    def _hide_settings_overlay(self):
        if getattr(self, "settings_overlay", None):
            self.settings_overlay.hide()

    def _handle_update_check(self, update_info):
        """Check if there's a new version available and show notification"""
        remote_version = (update_info.get("version") or "").strip()
        changelog = (update_info.get("changelog") or "").strip()
        
        if not remote_version or remote_version == self.current_version:
            return
        
        # Store update info
        self.available_update = {
            "version": remote_version,
            "changelog": changelog
        }
        
        # Show update overlay
        self._show_update_overlay()

    def _show_update_overlay(self):
        """Show update notification overlay"""
        if not self.updates_overlay:
            self._build_updates_overlay()
        
        if self.updates_overlay:
            self.updates_overlay.resize(self.chat_page.rect())
            self.updates_overlay.show()

    def _build_updates_overlay(self):
        """Build update notification overlay"""
        if not hasattr(self, "chat_page"):
            return
        
        panel = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(panel)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(12)

        title = QtWidgets.QLabel("Доступно обновление")
        title_font = QtGui.QFont("Segoe UI", 13, QtGui.QFont.Bold)
        title.setFont(title_font)
        layout.addWidget(title)

        # Version info
        version_info = QtWidgets.QLabel("")
        if self.available_update:
            version_text = f"Текущая версия: {self.current_version}\nНовая версия: {self.available_update['version']}"
            version_info.setText(version_text)
        version_info.setObjectName("mutedLabel")
        layout.addWidget(version_info)

        # Changelog
        changelog_label = QtWidgets.QLabel("Изменения:")
        changelog_label.setObjectName("mutedLabel")
        layout.addWidget(changelog_label)

        changelog_text = QtWidgets.QLabel("")
        if self.available_update:
            changelog_text.setText(self.available_update.get("changelog", ""))
        changelog_text.setWordWrap(True)
        changelog_text.setObjectName("mutedLabel")
        layout.addWidget(changelog_text)

        layout.addStretch(1)

        # Buttons
        buttons = QtWidgets.QHBoxLayout()
        buttons.setSpacing(8)
        buttons.addStretch(1)

        self.update_later_btn = QtWidgets.QPushButton("Позже")
        self.update_later_btn.setObjectName("ghostButton")
        self.update_later_btn.clicked.connect(self._close_update_overlay)
        buttons.addWidget(self.update_later_btn)

        self.update_now_btn = QtWidgets.QPushButton("Обновить")
        self.update_now_btn.setObjectName("primaryButton")
        self.update_now_btn.clicked.connect(self._start_update_download)
        buttons.addWidget(self.update_now_btn)

        layout.addLayout(buttons)

        self.updates_overlay = OverlayController(
            self.chat_page,
            self.chat_content,
            modal_size=QtCore.QSize(400, 300)
        )
        self.updates_overlay.set_content(panel)

    def _close_update_overlay(self):
        """Close update overlay"""
        if self.updates_overlay:
            self.updates_overlay.hide()

    def _start_update_download(self):
        """Start downloading the update from Google Drive"""
        if not UPDATES_JSON_URL or not UPDATE_EXE_URL:
            QtWidgets.QMessageBox.information(
                self,
                "Обновление",
                "Ссылки на файлы обновления не указаны.\n"
                "Пожалуйста, установите UPDATES_JSON_URL и UPDATE_EXE_URL в app/client.py"
            )
            self._close_update_overlay()
            return

        # Disable button and show progress
        self.update_now_btn.setEnabled(False)
        self.update_now_btn.setText("Проверяется версия...")

        # First download Updates.json to verify
        info_worker = UpdateInfoDownloadWorker(UPDATES_JSON_URL)
        info_worker.finished.connect(self._on_update_info_downloaded)
        info_worker.error.connect(self._on_update_info_error)
        info_worker.start()
        
        self.update_info_worker = info_worker

    def _on_update_info_downloaded(self, update_info):
        """Handle successful Updates.json download"""
        remote_version = update_info.get("version", "0.0.0")
        changelog = update_info.get("changelog", "")
        
        # Update the overlay with actual info from Google Drive
        if self.available_update:
            self.available_update["version"] = remote_version
            self.available_update["changelog"] = changelog
        
        # Now start downloading the EXE file
        self.update_now_btn.setText("Загружается...")
        
        exe_worker = UpdateDownloadWorker(UPDATE_EXE_URL, remote_version, "MAXutko.exe")
        exe_worker.finished.connect(self._on_update_download_finished)
        exe_worker.error.connect(self._on_update_download_error)
        exe_worker.progress.connect(self._on_update_download_progress)
        exe_worker.start()
        
        self.update_download_worker = exe_worker

    def _on_update_info_error(self, error_message):
        """Handle Updates.json download error"""
        self.update_now_btn.setEnabled(True)
        self.update_now_btn.setText("Обновить")
        QtWidgets.QMessageBox.warning(
            self,
            "Ошибка",
            f"Не удалось проверить обновления:\n{error_message}"
        )

    def _on_update_download_progress(self, downloaded, total):
        """Handle download progress"""
        if total > 0:
            percent = int(100 * downloaded / total)
            self.update_now_btn.setText(f"Загружается... {percent}%")

    def _on_update_download_finished(self, file_path, version):
        """Handle successful download"""
        try:
            # Create desktop shortcut
            self._create_desktop_shortcut(file_path, version)
            
            # Show success message
            QtWidgets.QMessageBox.information(
                self,
                "Успешно",
                f"Обновление загружено в папку Загрузки.\n"
                f"Ярлык создан на рабочем столе.\n"
                f"Запустите файл для обновления клиента."
            )
            
            self._close_update_overlay()
        except Exception as e:
            QtWidgets.QMessageBox.warning(
                self,
                "Ошибка",
                f"Не удалось создать ярлык: {str(e)}"
            )
            self.update_now_btn.setEnabled(True)
            self.update_now_btn.setText("Обновить")

    def _on_update_download_error(self, error_message):
        """Handle download error"""
        self.update_now_btn.setEnabled(True)
        self.update_now_btn.setText("Обновить")
        QtWidgets.QMessageBox.warning(
            self,
            "Ошибка загрузки",
            error_message
        )

    def _create_desktop_shortcut(self, exe_path, version):
        """Create a shortcut on the desktop (Windows only)"""
        try:
            # Try using pywin32 first
            import win32com.client
            
            desktop_path = os.path.join(os.path.expanduser("~"), "Desktop")
            shortcut_path = os.path.join(desktop_path, f"MAXutko_v{version}.lnk")
            
            shell = win32com.client.Dispatch("WScript.Shell")
            shortcut = shell.CreateShortCut(shortcut_path)
            shortcut.TargetPath = exe_path
            shortcut.WorkingDirectory = os.path.dirname(exe_path)
            shortcut.Description = f"MAXutko Messenger v{version}"
            shortcut.save()
        except ImportError:
            # Use PowerShell as fallback (available on all Windows versions)
            self._create_shortcut_powershell(exe_path, version)
        except Exception:
            # Last resort: create a batch file
            self._create_shortcut_batch(exe_path, version)

    def _create_shortcut_powershell(self, exe_path, version):
        """Create shortcut using PowerShell"""
        try:
            import subprocess
            
            desktop_path = os.path.join(os.path.expanduser("~"), "Desktop")
            shortcut_path = os.path.join(desktop_path, f"MAXutko_v{version}.lnk")
            
            # PowerShell script to create shortcut
            ps_script = f"""
$WshShell = New-Object -ComObject WScript.Shell
$Shortcut = $WshShell.CreateShortCut('{shortcut_path}')
$Shortcut.TargetPath = '{exe_path}'
$Shortcut.WorkingDirectory = '{os.path.dirname(exe_path)}'
$Shortcut.Description = 'MAXutko Messenger v{version}'
$Shortcut.Save()
"""
            
            # Run PowerShell
            subprocess.run(
                ["powershell", "-NoProfile", "-Command", ps_script],
                capture_output=True,
                timeout=5
            )
        except Exception:
            # Fallback to batch
            self._create_shortcut_batch(exe_path, version)

    def _create_shortcut_batch(self, exe_path, version):
        """Create a batch file that can run the update"""
        try:
            desktop_path = os.path.join(os.path.expanduser("~"), "Desktop")
            batch_path = os.path.join(desktop_path, f"MAXutko_v{version}_Update.bat")
            
            # Create batch file that runs the exe and waits
            batch_content = f"""@echo off
REM MAXutko Update v{version}
start "" "{exe_path}"
"""
            
            with open(batch_path, "w", encoding="utf-8") as f:
                f.write(batch_content)
        except Exception:
            pass


    def keyPressEvent(self, event):
        if event.key() == QtCore.Qt.Key_Escape:
            if getattr(self, "profile_overlay", None) and self.profile_overlay.is_visible():
                self.profile_overlay.hide()
                return
            if getattr(self, "settings_overlay", None) and self.settings_overlay.is_visible():
                self.settings_overlay.hide()
                return
            if getattr(self, "passcode_overlay", None) and self.passcode_overlay.is_visible():
                self.passcode_overlay.hide()
                return
            if getattr(self, "passcode_login_overlay", None) and self.passcode_login_overlay.is_visible():
                self.passcode_login_overlay.hide()
                return
            if getattr(self, "group_info_overlay", None) and self.group_info_overlay.is_visible():
                self.group_info_overlay.hide()
                return
        super().keyPressEvent(event)


def main():
    app = QtWidgets.QApplication(sys.argv)
    # Ensure the application (taskbar/minimized) icon is set early
    icon_path = resource_path("app/icon.ico")
    app_icon = QIcon(icon_path)
    app.setWindowIcon(app_icon)

    window = MAXutkoClient()
    # Also set the window icon (shown in the title bar)
    window.setWindowIcon(app_icon)
    window.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
