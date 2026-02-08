import socket
import threading
import json
import os
import re
import time
import hashlib
import secrets
import sys
from datetime import datetime

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.append(BASE_DIR)

from mailSystem.mailCla import MailSender, CodeGenerator


class MAXutkoServer:

    def __init__(self, host="0.0.0.0", port=5555):

        self.host = host
        self.port = port

        self.server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

        self.server.bind((self.host, self.port))
        self.server.listen(100)

        print(f"[+] Server started on {self.host}:{self.port}")

        self.clients = {}        # socket -> username
        self.clients_lock = threading.Lock()

        self.accounts_path = os.path.join(os.path.dirname(__file__), "accounts.json")
        self.accounts_lock = threading.Lock()
        self.accounts = self.load_accounts()

        self.pending = {}  # username -> {email, salt, hash, code, expires_at}
        self.pending_lock = threading.Lock()

        self.username_re = re.compile(r'^[A-Za-z0-9_]+$')
        self.email_re = re.compile(r'^[^@\s]+@[^@\s]+\.[^@\s]+$')

        self.mailer = MailSender()


    # ================= ACCOUNTS =================

    def load_accounts(self):

        if not os.path.exists(self.accounts_path):
            return {}

        try:
            with open(self.accounts_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                return data
        except:
            pass

        return {}


    def save_accounts(self):

        with open(self.accounts_path, "w", encoding="utf-8") as f:
            json.dump(self.accounts, f, ensure_ascii=False, indent=2)


    def _hash_password(self, password, salt):

        return hashlib.sha256((salt + password).encode()).hexdigest()


    def verify_password(self, stored, password):

        if not isinstance(stored, dict):
            return False

        salt = stored.get("salt", "")
        h = stored.get("hash", "")
        if not salt or not h:
            return False

        return self._hash_password(password, salt) == h


    def create_account(self, username, password, email):

        salt = secrets.token_hex(16)
        pw_hash = self._hash_password(password, salt)
        self.accounts[username] = {"salt": salt, "hash": pw_hash, "email": email}
        self.save_accounts()


    def send_auth(self, client, status, message=""):

        data = {
            "type": "auth",
            "status": status,
            "message": message
        }

        client.send((json.dumps(data) + "\n").encode())


    # ================= START =================

    def start(self):

        while True:

            client, addr = self.server.accept()

            print(f"[+] Connection from {addr}")

            threading.Thread(
                target=self.handle_client,
                args=(client,),
                daemon=True
            ).start()


    # ================= CLIENT =================

    def handle_client(self, client):

        buffer = ""

        try:
            # ---- LOGIN ----

            while "\n" not in buffer:

                data = client.recv(1024).decode()

                if not data:
                    client.close()
                    return

                buffer += data

            line, buffer = buffer.split("\n", 1)

            login = json.loads(line)

            username = login.get("username", "").strip()
            password = login.get("password", "")
            email = login.get("email", "").strip()
            action = login.get("action", "login")

            if not username or not password:
                self.send_auth(client, "error", "Missing username or password")
                client.close()
                return

            if not self.username_re.fullmatch(username):
                self.send_auth(client, "error", "Invalid username")
                client.close()
                return

            if action not in ("login", "register"):
                self.send_auth(client, "error", "Invalid action")
                client.close()
                return

            # ---- AUTH ----

            if action == "register":

                if not email or not self.email_re.fullmatch(email):
                    self.send_auth(client, "error", "Invalid email")
                    client.close()
                    return

                with self.accounts_lock:
                    if username in self.accounts:
                        self.send_auth(client, "error", "Username already exists")
                        client.close()
                        return

                code = CodeGenerator.generate_code(6)
                expires_at = time.time() + 300
                salt = secrets.token_hex(16)
                pw_hash = self._hash_password(password, salt)

                with self.pending_lock:
                    self.pending[username] = {
                        "email": email,
                        "salt": salt,
                        "hash": pw_hash,
                        "code": code,
                        "expires_at": expires_at
                    }

                self.mailer.send_code(email, code)
                self.send_auth(client, "need_code", "Code sent to email")

                # Wait for verify
                while True:
                    data = client.recv(1024).decode()
                    if not data:
                        client.close()
                        return

                    buffer += data
                    if "\n" not in buffer:
                        continue

                    line, buffer = buffer.split("\n", 1)
                    if not line.strip():
                        continue

                    verify = json.loads(line)
                    if verify.get("action") != "verify":
                        self.send_auth(client, "error", "Invalid action")
                        client.close()
                        return

                    v_user = verify.get("username", "").strip()
                    v_code = verify.get("code", "").strip()

                    with self.pending_lock:
                        pending = self.pending.get(v_user)

                        if not pending:
                            self.send_auth(client, "error", "No pending code")
                            client.close()
                            return

                        if time.time() > pending["expires_at"]:
                            del self.pending[v_user]
                            self.send_auth(client, "error", "Code expired")
                            client.close()
                            return

                        if v_code != pending["code"]:
                            self.send_auth(client, "error", "Wrong code")
                            client.close()
                            return

                        del self.pending[v_user]

                    with self.accounts_lock:
                        self.accounts[v_user] = {
                            "salt": pending["salt"],
                            "hash": pending["hash"],
                            "email": pending["email"]
                        }
                        self.save_accounts()

                    self.send_auth(client, "ok", "Verified")
                    client.close()
                    return

            else:
                with self.accounts_lock:
                    stored = self.accounts.get(username)

                if not stored:
                    self.send_auth(client, "error", "User not found")
                    client.close()
                    return

                if not self.verify_password(stored, password):
                    self.send_auth(client, "error", "Wrong password")
                    client.close()
                    return

            # ---- REGISTER CLIENT ----

            with self.clients_lock:

                if username in self.clients.values():

                    self.send_auth(client, "error", "Username already taken")
                    client.close()
                    return

                self.clients[client] = username

            self.send_auth(client, "ok")

            print(f"[+] {username} logged in")

            self.send_system(f"{username} joined the chat")

            # ---- MAIN LOOP ----

            while True:

                data = client.recv(1024).decode()

                if not data:
                    break

                buffer += data

                while "\n" in buffer:

                    line, buffer = buffer.split("\n", 1)

                    if not line.strip():
                        continue

                    msg = json.loads(line)

                    text = msg.get("message")

                    if not text:
                        continue

                    self.broadcast_message(username, text)

        except Exception as e:

            print("Client error:", e)

        finally:

            self.remove_client(client)


    # ================= BROADCAST =================

    def broadcast_message(self, username, text):

        data = {
            "username": username,
            "message": text,
            "timestamp": datetime.now().strftime("%H:%M:%S"),
            "type": "message"
        }

        raw = (json.dumps(data) + "\n").encode()

        with self.clients_lock:

            for c in list(self.clients.keys()):

                try:
                    c.send(raw)
                except:
                    pass


    def send_system(self, text):

        data = {
            "username": "SYSTEM",
            "message": text,
            "timestamp": "",
            "type": "system"
        }

        raw = (json.dumps(data) + "\n").encode()

        with self.clients_lock:

            for c in list(self.clients.keys()):

                try:
                    c.send(raw)
                except:
                    pass


    # ================= REMOVE =================

    def remove_client(self, client):

        with self.clients_lock:

            if client not in self.clients:
                return

            username = self.clients[client]

            del self.clients[client]

        try:
            client.close()
        except:
            pass

        print(f"[-] {username} disconnected")

        self.send_system(f"{username} left the chat")


# ================= MAIN =================


def main():

    server = MAXutkoServer(port=5555)

    server.start()


if __name__ == "__main__":
    main()
