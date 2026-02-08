import sys
import socket
import threading
import json
import re
from PyQt5 import QtCore, QtWidgets, QtGui


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


class MAXutkoClient(QtWidgets.QMainWindow):

    def __init__(self):
        super().__init__()

        self.setWindowTitle("MAXutko Messenger")
        self.resize(800, 600)
        self.setMinimumSize(600, 500)

        # Socket
        self.client_socket = None
        self.running = False
        self.prebuffer = ""

        self.host = "127.0.0.1"
        self.port = 5555
        self.username = ""
        self.password = ""
        self.email = ""
        self.auth_mode = "login"
        self.pending_socket = None
        self.receiver_thread = None

        self.setup_ui()
        self.show_login_screen()

    # ================= UI =================

    def setup_ui(self):
        self.central = QtWidgets.QWidget()
        self.setCentralWidget(self.central)

        self.layout = QtWidgets.QVBoxLayout(self.central)
        self.layout.setContentsMargins(20, 20, 20, 20)

        self.stack = QtWidgets.QStackedWidget()
        self.layout.addWidget(self.stack)

        self.login_page = QtWidgets.QWidget()
        self.chat_page = QtWidgets.QWidget()
        self.stack.addWidget(self.login_page)
        self.stack.addWidget(self.chat_page)

        self._build_login_page()
        self._build_chat_page()

        self._load_qss()

    def _load_qss(self):
        qss_path = QtCore.QFileInfo(__file__).absolutePath() + "/ui.qss"
        try:
            with open(qss_path, "r", encoding="utf-8") as f:
                QtWidgets.QApplication.instance().setStyleSheet(f.read())
        except Exception:
            pass

    def _build_login_page(self):
        root_layout = QtWidgets.QVBoxLayout(self.login_page)
        root_layout.setAlignment(QtCore.Qt.AlignCenter)

        card = QtWidgets.QFrame()
        card.setObjectName("loginCard")
        card_layout = QtWidgets.QVBoxLayout(card)
        card_layout.setContentsMargins(24, 22, 24, 22)
        card_layout.setSpacing(12)

        title = QtWidgets.QLabel("MAXutko Messenger")
        title.setObjectName("titleLabel")
        title.setAlignment(QtCore.Qt.AlignCenter)
        title_font = QtGui.QFont("Segoe UI", 24, QtGui.QFont.Bold)
        title.setFont(title_font)
        card_layout.addWidget(title)

        # Username
        self.username_input = QtWidgets.QLineEdit()
        self.username_input.setPlaceholderText("Enter username")
        self.username_input.setObjectName("input")

        # Password
        self.password_input = QtWidgets.QLineEdit()
        self.password_input.setPlaceholderText("Enter password")
        self.password_input.setEchoMode(QtWidgets.QLineEdit.Password)
        self.password_input.setObjectName("input")

        # Email
        self.email_row = QtWidgets.QWidget()
        email_layout = QtWidgets.QHBoxLayout(self.email_row)
        email_layout.setContentsMargins(0, 0, 0, 0)
        email_layout.setSpacing(8)
        self.email_input = QtWidgets.QLineEdit()
        self.email_input.setPlaceholderText("Enter email")
        self.email_input.setObjectName("input")
        email_layout.addWidget(self._labeled("Email:", self.email_input))

        # Server
        self.server_input = QtWidgets.QLineEdit("127.0.0.1:5555")
        self.server_input.setPlaceholderText("host:port")
        self.server_input.setObjectName("input")

        card_layout.addWidget(self._labeled("Username:", self.username_input))
        card_layout.addWidget(self._labeled("Password:", self.password_input))
        card_layout.addWidget(self.email_row)
        card_layout.addWidget(self._labeled("Server:", self.server_input))

        # Mode
        mode_row = QtWidgets.QWidget()
        mode_layout = QtWidgets.QHBoxLayout(mode_row)
        mode_layout.setContentsMargins(0, 0, 0, 0)
        mode_layout.setSpacing(12)

        self.login_radio = QtWidgets.QRadioButton("Login")
        self.register_radio = QtWidgets.QRadioButton("Register")
        self.login_radio.setChecked(True)
        self.mode_group = QtWidgets.QButtonGroup()
        self.mode_group.addButton(self.login_radio)
        self.mode_group.addButton(self.register_radio)
        self.login_radio.toggled.connect(self._sync_auth_mode)

        mode_layout.addWidget(self.login_radio)
        mode_layout.addWidget(self.register_radio)
        mode_layout.addStretch(1)
        card_layout.addWidget(mode_row)

        # Button
        self.connect_btn = QtWidgets.QPushButton("Login")
        self.connect_btn.setObjectName("primaryButton")
        self.connect_btn.clicked.connect(self.connect_to_server)
        card_layout.addWidget(self.connect_btn)

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
        self.code_input.setPlaceholderText("Verification code")
        self.code_input.setObjectName("input")
        self.verify_btn = QtWidgets.QPushButton("Verify")
        self.verify_btn.setObjectName("primaryButton")
        self.verify_btn.clicked.connect(self.verify_code)
        code_layout.addWidget(self._labeled("Code:", self.code_input))
        code_layout.addWidget(self.verify_btn)
        card_layout.addWidget(self.code_row)

        root_layout.addWidget(card, 0, QtCore.Qt.AlignCenter)

        self.email_row.setVisible(False)
        self.code_row.setVisible(False)

    def _build_chat_page(self):
        layout = QtWidgets.QVBoxLayout(self.chat_page)
        layout.setSpacing(10)
        layout.setContentsMargins(0, 0, 0, 0)

        # Top bar
        top_bar = QtWidgets.QFrame()
        top_bar.setObjectName("topBar")
        top_layout = QtWidgets.QHBoxLayout(top_bar)
        top_layout.setContentsMargins(12, 8, 12, 8)

        self.user_label = QtWidgets.QLabel("")
        self.user_label.setObjectName("mutedLabel")
        top_layout.addWidget(self.user_label)
        top_layout.addStretch(1)

        self.disconnect_btn = QtWidgets.QPushButton("Disconnect")
        self.disconnect_btn.setObjectName("dangerButton")
        self.disconnect_btn.clicked.connect(self.disconnect)
        top_layout.addWidget(self.disconnect_btn)

        layout.addWidget(top_bar)

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
        layout.addWidget(self.scroll_area, 1)

        # Bottom
        bottom = QtWidgets.QWidget()
        bottom_layout = QtWidgets.QHBoxLayout(bottom)
        bottom_layout.setContentsMargins(0, 0, 0, 0)
        bottom_layout.setSpacing(8)

        self.message_input = MessageInput()
        self.message_input.setObjectName("inputArea")
        self.message_input.setFixedHeight(70)
        self.message_input.send_requested.connect(self.send_message)

        self.send_btn = QtWidgets.QPushButton("Send")
        self.send_btn.setObjectName("primaryButton")
        self.send_btn.clicked.connect(self.send_message)

        bottom_layout.addWidget(self.message_input, 1)
        bottom_layout.addWidget(self.send_btn)

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
        self.stack.setCurrentWidget(self.login_page)
        self.message_input.clear()
        self._clear_messages()

    def show_chat_screen(self):
        self.user_label.setText(f"{self.username} @ {self.host}:{self.port}")
        self.stack.setCurrentWidget(self.chat_page)
        self._clear_messages()

    # ================= CONNECT =================

    def _sync_auth_mode(self):
        is_register = self.register_radio.isChecked()
        self.auth_mode = "register" if is_register else "login"
        self.connect_btn.setText("Register" if is_register else "Login")
        self.email_row.setVisible(is_register)
        self.code_row.setVisible(False)

    def connect_to_server(self):
        self.username = self.username_input.text().strip()
        self.password = self.password_input.text().strip()
        self.email = self.email_input.text().strip()
        server = self.server_input.text().strip()
        self.status_label.setText("")

        if not self.username:
            QtWidgets.QMessageBox.warning(self, "Error", "Enter username")
            return
        if not self.password:
            QtWidgets.QMessageBox.warning(self, "Error", "Enter password")
            return
        if self.auth_mode == "register" and not self.email:
            QtWidgets.QMessageBox.warning(self, "Error", "Enter email")
            return

        if not re.match(r'^[A-Za-z0-9_]+$', self.username):
            QtWidgets.QMessageBox.warning(self, "Error", "Only latin, numbers and _")
            return

        try:
            host, port = server.split(":")
            self.host = host
            self.port = int(port)
        except Exception:
            QtWidgets.QMessageBox.critical(self, "Error", "Wrong server format")
            return

        try:
            self.client_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.client_socket.connect((self.host, self.port))

            login_data = json.dumps({
                "action": self.auth_mode,
                "username": self.username,
                "password": self.password,
                "email": self.email
            }) + "\n"

            self.client_socket.send(login_data.encode())

            buffer = ""
            while "\n" not in buffer:
                data = self.client_socket.recv(1024).decode()
                if not data:
                    raise Exception("Server closed")
                buffer += data

            line, rest = buffer.split("\n", 1)
            resp = json.loads(line)

            if resp.get("type") != "auth":
                raise Exception("Auth failed")

            if resp.get("status") == "need_code":
                self.pending_socket = self.client_socket
                self.status_label.setText("Code sent to email")
                self.code_row.setVisible(True)
                self.connect_btn.setEnabled(False)
                return

            if resp.get("status") != "ok":
                msg = resp.get("message", "Auth failed")
                self.client_socket.close()
                QtWidgets.QMessageBox.critical(self, "Error", msg)
                return

            self.prebuffer = rest
            self.running = True

            self.show_chat_screen()

            self.receiver_thread = ReceiverThread(self.client_socket, self.prebuffer)
            self.receiver_thread.message_received.connect(self.display_message)
            self.receiver_thread.connection_lost.connect(self.connection_lost)
            self.receiver_thread.start()

        except Exception as e:
            QtWidgets.QMessageBox.critical(self, "Error", str(e))

    def verify_code(self):
        code = self.code_input.text().strip()
        if not code:
            self.status_label.setText("Enter code")
            return

        try:
            if not self.pending_socket:
                self.status_label.setText("No pending verification")
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
                self.show_login_screen("Welcome!")
                return

            msg = resp.get("message", "Verification failed")
            self.status_label.setText(msg)

        except Exception as e:
            self.status_label.setText(str(e))

    # ================= SEND =================

    def send_message(self):
        msg = self.message_input.toPlainText().strip()
        if not msg:
            return

        try:
            data = json.dumps({
                "message": msg
            }) + "\n"
            self.client_socket.send(data.encode())
            self.message_input.clear()
        except Exception:
            QtWidgets.QMessageBox.critical(self, "Error", "Send failed")

    # ================= DISPLAY =================

    def display_message(self, data):
        if data.get("type") == "history":
            items = data.get("items", [])
            for item in items:
                if isinstance(item, dict):
                    self.display_message(item)
            return

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
            name = "You" if is_me else user
            meta = QtWidgets.QLabel(f"{name} • {time}")
            meta.setObjectName("mutedLabel")
            body = QtWidgets.QLabel(text)
            body.setWordWrap(True)
            msg_layout.addWidget(meta)
            msg_layout.addWidget(body)
            alignment = QtCore.Qt.AlignRight if is_me else QtCore.Qt.AlignLeft
            msg_type = "chat"

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

    def disconnect(self):
        self.running = False
        if self.receiver_thread:
            self.receiver_thread.stop()
            self.receiver_thread = None
        try:
            if self.client_socket:
                self.client_socket.close()
        except Exception:
            pass
        self.show_login_screen()

    def connection_lost(self):
        if self.running:
            self.running = False
            QtWidgets.QMessageBox.information(self, "Info", "Connection lost")
            self.show_login_screen()

    def closeEvent(self, event):
        self.running = False
        if self.receiver_thread:
            self.receiver_thread.stop()
            self.receiver_thread = None
        try:
            if self.client_socket:
                self.client_socket.close()
        except Exception:
            pass
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
