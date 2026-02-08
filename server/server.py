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

        self.notes_path = os.path.join(os.path.dirname(__file__), "notes.json")
        self.notes_lock = threading.Lock()
        self.notes = self.load_notes()

        self.history_path = os.path.join(os.path.dirname(__file__), "history.json")
        self.history_lock = threading.Lock()
        self.history_limit = 5000
        self.history = self.load_history()

        self.dm_history_path = os.path.join(os.path.dirname(__file__), "dm_history.json")
        self.dm_history_lock = threading.Lock()
        self.dm_history_limit = 20000
        self.dm_history = self.load_dm_history()

        self.receipts_path = os.path.join(os.path.dirname(__file__), "dm_receipts.json")
        self.receipts_lock = threading.Lock()
        self.receipts_pending = self.load_receipts()

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
                for name, item in data.items():
                    data[name] = self._normalize_account(item)
                return data
        except:
            pass

        return {}

    def _normalize_account(self, data):
        if not isinstance(data, dict):
            return {
                "salt": "",
                "hash": "",
                "email": "",
                "profile": {"nick": "", "about": "", "avatar": ""}
            }
        profile = data.get("profile")
        if not isinstance(profile, dict):
            profile = {}
        profile.setdefault("nick", "")
        profile.setdefault("about", "")
        profile.setdefault("avatar", "")
        data["profile"] = profile
        data.setdefault("salt", "")
        data.setdefault("hash", "")
        data.setdefault("email", "")
        return data


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
        self.accounts[username] = {
            "salt": salt,
            "hash": pw_hash,
            "email": email,
            "profile": {"nick": "", "about": "", "avatar": ""}
        }
        self.save_accounts()


    def send_auth(self, client, status, message=""):

        data = {
            "type": "auth",
            "status": status,
            "message": message
        }

        client.send((json.dumps(data) + "\n").encode())

    # ================= NOTES =================

    def load_notes(self):

        if not os.path.exists(self.notes_path):
            return {}

        try:
            with open(self.notes_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                return data
        except:
            pass

        return {}


    def save_notes(self):

        with open(self.notes_path, "w", encoding="utf-8") as f:
            json.dump(self.notes, f, ensure_ascii=False, indent=2)


    def get_note(self, owner, target):
        with self.notes_lock:
            user_notes = self.notes.get(owner, {})
            if not isinstance(user_notes, dict):
                return ""
            return user_notes.get(target, "")


    def set_note(self, owner, target, note):
        with self.notes_lock:
            user_notes = self.notes.setdefault(owner, {})
            if not isinstance(user_notes, dict):
                user_notes = {}
                self.notes[owner] = user_notes
            user_notes[target] = note
            self.save_notes()

    # ================= HISTORY =================

    def load_history(self):

        if not os.path.exists(self.history_path):
            return []

        try:
            with open(self.history_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, list):
                return data
        except:
            pass

        return []


    def save_history(self):

        with open(self.history_path, "w", encoding="utf-8") as f:
            json.dump(self.history, f, ensure_ascii=False, indent=2)


    def append_history(self, data):

        with self.history_lock:
            self.history.append(data)
            if len(self.history) > self.history_limit:
                self.history = self.history[-self.history_limit:]
            self.save_history()


    def send_history(self, client):

        with self.history_lock:
            items = list(self.history)

        payload = {
            "type": "history",
            "items": items
        }

        client.send((json.dumps(payload) + "\n").encode())

    # ================= DM HISTORY =================

    def load_dm_history(self):

        if not os.path.exists(self.dm_history_path):
            return []

        try:
            with open(self.dm_history_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, list):
                return data
        except:
            pass

        return []


    def save_dm_history(self):

        with open(self.dm_history_path, "w", encoding="utf-8") as f:
            json.dump(self.dm_history, f, ensure_ascii=False, indent=2)


    def append_dm_history(self, data):

        with self.dm_history_lock:
            self.dm_history.append(data)
            if len(self.dm_history) > self.dm_history_limit:
                self.dm_history = self.dm_history[-self.dm_history_limit:]
            self.save_dm_history()


    def _find_dm_by_id(self, msg_id):

        for msg in self.dm_history:
            if msg.get("id") == msg_id:
                return msg
        return None


    def update_dm_status(self, msg_id, delivered=None, read=None):

        with self.dm_history_lock:
            msg = self._find_dm_by_id(msg_id)
            if not msg:
                return None
            if delivered is not None:
                msg["delivered"] = bool(delivered)
            if read is not None:
                msg["read"] = bool(read)
            self.save_dm_history()
            return dict(msg)


    def send_dm_history(self, client, username):

        to_notify = []
        items = []

        with self.dm_history_lock:
            for msg in self.dm_history:
                if msg.get("type") != "dm":
                    continue
                if msg.get("username") == username or msg.get("to") == username:
                    if msg.get("to") == username and not msg.get("delivered", False):
                        msg["delivered"] = True
                        to_notify.append({
                            "sender": msg.get("username"),
                            "id": msg.get("id"),
                            "delivered": True,
                            "read": bool(msg.get("read", False))
                        })
                    items.append(dict(msg))
            if to_notify:
                self.save_dm_history()

        payload = {
            "type": "dm_history",
            "items": items
        }
        try:
            client.send((json.dumps(payload) + "\n").encode())
        except:
            pass

        for item in to_notify:
            sender = item.get("sender")
            if sender:
                self.send_dm_receipt(sender, item.get("id"), item.get("delivered"), item.get("read"))

    # ================= RECEIPTS =================

    def load_receipts(self):

        if not os.path.exists(self.receipts_path):
            return {}

        try:
            with open(self.receipts_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                return data
        except:
            pass

        return {}


    def save_receipts(self):

        with open(self.receipts_path, "w", encoding="utf-8") as f:
            json.dump(self.receipts_pending, f, ensure_ascii=False, indent=2)


    def send_dm_receipt(self, username, msg_id, delivered=None, read=None):

        if not msg_id:
            return

        payload = {
            "type": "dm_receipt",
            "id": msg_id
        }
        if delivered is not None:
            payload["delivered"] = bool(delivered)
        if read is not None:
            payload["read"] = bool(read)

        target_sock = self._socket_by_username(username)
        if target_sock:
            try:
                target_sock.send((json.dumps(payload) + "\n").encode())
            except:
                pass
            return

        with self.receipts_lock:
            user_receipts = self.receipts_pending.setdefault(username, {})
            existing = user_receipts.get(msg_id, {})
            if delivered is not None:
                existing["delivered"] = bool(delivered)
            if read is not None:
                existing["read"] = bool(read)
            user_receipts[msg_id] = existing
            self.save_receipts()


    def send_pending_receipts(self, client, username):

        with self.receipts_lock:
            items = self.receipts_pending.pop(username, {})
            if items:
                self.save_receipts()

        for msg_id, status in items.items():
            payload = {
                "type": "dm_receipt",
                "id": msg_id
            }
            if "delivered" in status:
                payload["delivered"] = bool(status["delivered"])
            if "read" in status:
                payload["read"] = bool(status["read"])
            try:
                client.send((json.dumps(payload) + "\n").encode())
            except:
                pass
    # ================= USERS =================

    def _build_users_payload(self):
        with self.accounts_lock:
            all_users = set(self.accounts.keys())

        with self.clients_lock:
            active_users = set(self.clients.values())

        all_users |= active_users

        users = []
        for name in all_users:
            profile = {}
            with self.accounts_lock:
                acc = self.accounts.get(name, {})
                profile = dict(acc.get("profile") or {})
            users.append({
                "username": name,
                "active": name in active_users,
                "nick": profile.get("nick", ""),
                "avatar": profile.get("avatar", "")
            })

        return {
            "type": "users",
            "users": users
        }

    def send_user_list(self, target_client=None):
        payload = self._build_users_payload()
        raw = (json.dumps(payload) + "\n").encode()

        if target_client is not None:
            try:
                target_client.send(raw)
            except:
                pass
            return

        with self.clients_lock:
            for c in list(self.clients.keys()):
                try:
                    c.send(raw)
                except:
                    pass


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
                            "email": pending["email"],
                            "profile": {"nick": "", "about": "", "avatar": ""}
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

            self.send_history(client)
            self.send_dm_history(client, username)
            self.send_pending_receipts(client, username)
            self.send_user_list(client)

            self.send_system(f"{username} joined the chat")
            self.send_user_list()

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

                    msg_type = msg.get("type")
                    if msg_type == "dm_read":
                        msg_id = msg.get("id", "")
                        self.mark_dm_read(username, msg_id)
                        continue

                    action = msg.get("action")
                    if action:
                        if action == "get_profile":
                            target = (msg.get("username") or "").strip()
                            with self.accounts_lock:
                                acc = self.accounts.get(target)
                                if acc:
                                    profile = dict(acc.get("profile") or {})
                                else:
                                    profile = {"nick": "", "about": "", "avatar": ""}
                            payload = {
                                "type": "profile",
                                "username": target,
                                "profile": profile
                            }
                            try:
                                client.send((json.dumps(payload) + "\n").encode())
                            except:
                                pass
                            continue
                        if action == "update_profile":
                            nick = msg.get("nick")
                            about = msg.get("about")
                            avatar = msg.get("avatar")
                            with self.accounts_lock:
                                acc = self.accounts.get(username)
                                if acc:
                                    profile = dict(acc.get("profile") or {})
                                    if isinstance(nick, str):
                                        profile["nick"] = nick[:32]
                                    if isinstance(about, str):
                                        profile["about"] = about[:500]
                                    if isinstance(avatar, str):
                                        profile["avatar"] = avatar
                                    acc["profile"] = profile
                                    self.accounts[username] = acc
                                    self.save_accounts()
                            self.send_user_list()
                            continue
                        if action == "get_note":
                            target = (msg.get("username") or "").strip()
                            note = self.get_note(username, target) if target else ""
                            payload = {
                                "type": "note",
                                "username": target,
                                "note": note
                            }
                            try:
                                client.send((json.dumps(payload) + "\n").encode())
                            except:
                                pass
                            continue
                        if action == "set_note":
                            target = (msg.get("username") or "").strip()
                            note = msg.get("note")
                            if target and isinstance(note, str):
                                self.set_note(username, target, note[:1000])
                            continue

                    text = msg.get("message")
                    target = msg.get("to", "").strip()

                    if not text:
                        continue

                    if target:
                        self.send_direct_message(username, target, text)
                    else:
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

        self.append_history(data)

        raw = (json.dumps(data) + "\n").encode()

        with self.clients_lock:

            for c in list(self.clients.keys()):

                try:
                    c.send(raw)
                except:
                    pass


    def _socket_by_username(self, username):

        with self.clients_lock:
            for sock, name in self.clients.items():
                if name == username:
                    return sock
        return None


    def send_direct_message(self, sender, target, text):

        msg_id = secrets.token_hex(12)
        delivered = self._socket_by_username(target) is not None
        data = {
            "id": msg_id,
            "username": sender,
            "to": target,
            "message": text,
            "timestamp": datetime.now().strftime("%H:%M:%S"),
            "type": "dm",
            "delivered": delivered,
            "read": False
        }

        self.append_dm_history(dict(data))

        raw = (json.dumps(data) + "\n").encode()

        target_sock = self._socket_by_username(target)
        sender_sock = self._socket_by_username(sender)

        if target_sock:
            try:
                target_sock.send(raw)
            except:
                pass
        if sender_sock:
            try:
                sender_sock.send(raw)
            except:
                pass


    def mark_dm_read(self, username, msg_id):

        if not msg_id:
            return

        with self.dm_history_lock:
            msg = self._find_dm_by_id(msg_id)
            if not msg:
                return
            if msg.get("to") != username:
                return
            if msg.get("read"):
                return
            msg["read"] = True
            self.save_dm_history()
            updated = dict(msg)

        sender = updated.get("username")
        if sender:
            self.send_dm_receipt(sender, updated.get("id"), updated.get("delivered"), True)


    def send_system(self, text):

        data = {
            "username": "SYSTEM",
            "message": text,
            "timestamp": "",
            "type": "system"
        }

        self.append_history(data)

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
        self.send_user_list()


# ================= MAIN =================


def main():

    server = MAXutkoServer(port=5555)

    server.start()


if __name__ == "__main__":
    main()
