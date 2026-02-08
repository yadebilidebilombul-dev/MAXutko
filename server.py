import socket
import threading
import json
import os
import re
import hashlib
import secrets
from datetime import datetime


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

        self.accounts_path = "accounts.json"
        self.accounts_lock = threading.Lock()
        self.accounts = self.load_accounts()

        self.username_re = re.compile(r'^[A-Za-z0-9_]+$')

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

        # New format: {"salt": "...", "hash": "..."}
        if isinstance(stored, dict):
            salt = stored.get("salt", "")
            h = stored.get("hash", "")
            if salt and h:
                return self._hash_password(password, salt) == h, False
            return False, False

        # Legacy format: stored is a string (plain or sha256 hex)
        if isinstance(stored, str):
            if password == stored:
                return True, True
            if re.fullmatch(r"[0-9a-fA-F]{64}", stored):
                if hashlib.sha256(password.encode()).hexdigest() == stored:
                    return True, True
        return False, False


    def create_account(self, username, password):

        salt = secrets.token_hex(16)
        pw_hash = self._hash_password(password, salt)
        self.accounts[username] = {"salt": salt, "hash": pw_hash}
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

            with self.accounts_lock:

                if action == "register":

                    if username in self.accounts:
                        self.send_auth(client, "error", "Username already exists")
                        client.close()
                        return

                    self.create_account(username, password)

                else:
                    stored = self.accounts.get(username)

                    if not stored:
                        self.send_auth(client, "error", "User not found")
                        client.close()
                        return

                    ok, upgrade = self.verify_password(stored, password)

                    if not ok:
                        self.send_auth(client, "error", "Wrong password")
                        client.close()
                        return

                    if upgrade:
                        self.create_account(username, password)

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
