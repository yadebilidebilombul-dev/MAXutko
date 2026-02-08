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

from mailSystem.mailCla import MailSender, CodeGenerator, is_console_email


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

        self.contacts_path = os.path.join(os.path.dirname(__file__), "contacts.json")
        self.contacts_lock = threading.Lock()
        self.contacts = self.load_contacts()

        self.receipts_path = os.path.join(os.path.dirname(__file__), "dm_receipts.json")
        self.receipts_lock = threading.Lock()
        self.receipts_pending = self.load_receipts()

        self.pending = {}  # username -> {email, salt, hash, code, expires_at}
        self.pending_lock = threading.Lock()
        self.reset_pending = {}  # username -> {email, code, expires_at}
        self.reset_pending_lock = threading.Lock()
        self.tokens_path = os.path.join(os.path.dirname(__file__), "tokens.json")
        self.tokens_lock = threading.Lock()
        self.tokens = self.load_tokens()
        self.token_ttl_seconds = 30 * 24 * 60 * 60

        self.username_re = re.compile(r'^[A-Za-z0-9_]+$')
        self.email_re = re.compile(r'^[^@\s]+@[^@\s]+\.[^@\s]+$')

        self.mailer = MailSender()
        self.rate_limits = {}
        self.rate_limits_lock = threading.Lock()


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

    def load_tokens(self):
        if not os.path.exists(self.tokens_path):
            return {}
        try:
            with open(self.tokens_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                now = time.time()
                cleaned = {}
                for token, item in data.items():
                    if not isinstance(item, dict):
                        continue
                    if item.get("expires_at", 0) > now and item.get("username"):
                        cleaned[token] = item
                return cleaned
        except:
            pass
        return {}

    def save_tokens(self):
        with open(self.tokens_path, "w", encoding="utf-8") as f:
            json.dump(self.tokens, f, ensure_ascii=False, indent=2)

    def _issue_token(self, username):
        token = secrets.token_hex(24)
        expires_at = time.time() + self.token_ttl_seconds
        with self.tokens_lock:
            to_remove = [t for t, v in self.tokens.items() if v.get("username") == username]
            for t in to_remove:
                self.tokens.pop(t, None)
            self.tokens[token] = {"username": username, "expires_at": expires_at}
            self.save_tokens()
        return token

    def _username_by_token(self, token):
        if not token:
            return None
        now = time.time()
        with self.tokens_lock:
            item = self.tokens.get(token)
            if not item:
                return None
            if item.get("expires_at", 0) <= now:
                self.tokens.pop(token, None)
                self.save_tokens()
                return None
            return item.get("username")


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

    def _email_in_use(self, email, exclude_username=None):
        if not email:
            return False
        target = email.strip().lower()
        with self.accounts_lock:
            for name, acc in self.accounts.items():
                if exclude_username and name == exclude_username:
                    continue
                acc_email = (acc.get("email") or "").strip().lower()
                if acc_email and acc_email == target:
                    return True
        return False


    def send_auth(self, client, status, message="", token="", username=""):

        data = {
            "type": "auth",
            "status": status,
            "message": message
        }
        if token:
            data["token"] = token
        if username:
            data["username"] = username

        client.send((json.dumps(data) + "\n").encode())

    def _rate_limit(self, key, limit, window_seconds):
        now = time.time()
        with self.rate_limits_lock:
            bucket = self.rate_limits.setdefault(key, [])
            bucket = [t for t in bucket if now - t < window_seconds]
            if len(bucket) >= limit:
                self.rate_limits[key] = bucket
                return False
            bucket.append(now)
            self.rate_limits[key] = bucket
            return True

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

    # ================= CONTACTS =================

    def load_contacts(self):
        if not os.path.exists(self.contacts_path):
            contacts = {}
            # Auto-create contacts from DM history on first run
            try:
                with self.dm_history_lock:
                    items = list(self.dm_history)
            except Exception:
                items = []
            for msg in items:
                if not isinstance(msg, dict):
                    continue
                if msg.get("type") != "dm":
                    continue
                a = (msg.get("username") or "").strip()
                b = (msg.get("to") or "").strip()
                if not a or not b or a == b:
                    continue
                contacts.setdefault(a, [])
                contacts.setdefault(b, [])
                if b not in contacts[a]:
                    contacts[a].append(b)
                if a not in contacts[b]:
                    contacts[b].append(a)
            if contacts:
                self.contacts = contacts
                self.save_contacts()
            return contacts
        try:
            with open(self.contacts_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                cleaned = {}
                for name, items in data.items():
                    if not isinstance(name, str):
                        continue
                    if isinstance(items, list):
                        cleaned[name] = [str(x) for x in items if str(x)]
                return cleaned
        except:
            pass
        return {}

    def save_contacts(self):
        with open(self.contacts_path, "w", encoding="utf-8") as f:
            json.dump(self.contacts, f, ensure_ascii=False, indent=2)

    def _user_exists(self, username):
        if not username:
            return False
        with self.accounts_lock:
            if username in self.accounts:
                return True
        with self.clients_lock:
            return username in self.clients.values()

    def _add_contact_pair(self, user_a, user_b):
        if not user_a or not user_b or user_a == user_b:
            return
        if not self._user_exists(user_b):
            return
        with self.contacts_lock:
            a_list = self.contacts.setdefault(user_a, [])
            b_list = self.contacts.setdefault(user_b, [])
            if user_b not in a_list:
                a_list.append(user_b)
            if user_a not in b_list:
                b_list.append(user_a)
            self.contacts[user_a] = a_list
            self.contacts[user_b] = b_list
            self.save_contacts()

    def _remove_contact_pair(self, user_a, user_b):
        if not user_a or not user_b or user_a == user_b:
            return
        with self.contacts_lock:
            a_list = self.contacts.get(user_a, [])
            b_list = self.contacts.get(user_b, [])
            if user_b in a_list:
                a_list = [x for x in a_list if x != user_b]
                self.contacts[user_a] = a_list
            if user_a in b_list:
                b_list = [x for x in b_list if x != user_a]
                self.contacts[user_b] = b_list
            self.save_contacts()

    def _build_contacts_payload(self, username):
        with self.contacts_lock:
            contacts = list(self.contacts.get(username, []))

        with self.accounts_lock:
            profiles = {
                name: dict((acc.get("profile") or {}))
                for name, acc in self.accounts.items()
            }

        with self.clients_lock:
            active_users = set(self.clients.values())

        items = []
        for name in contacts:
            profile = profiles.get(name, {})
            items.append({
                "username": name,
                "active": name in active_users,
                "nick": profile.get("nick", "")
            })

        return {
            "type": "contacts",
            "contacts": items
        }

    def send_contacts(self, target_client, username):
        payload = self._build_contacts_payload(username)
        try:
            target_client.send((json.dumps(payload) + "\n").encode())
        except:
            pass

    def send_all_contacts(self):
        with self.clients_lock:
            items = list(self.clients.items())
        for sock, name in items:
            self.send_contacts(sock, name)


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
                "nick": profile.get("nick", "")
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

    def _search_users(self, query, requester=""):
        q = (query or "").strip().casefold()
        if not q:
            return []
        with self.accounts_lock:
            accounts = dict(self.accounts)
        with self.clients_lock:
            active_users = set(self.clients.values())

        results = []
        for name, acc in accounts.items():
            if requester and name == requester:
                continue
            profile = acc.get("profile") or {}
            nick = (profile.get("nick") or "").strip()
            if q in name.casefold() or (nick and q in nick.casefold()):
                results.append({
                    "username": name,
                    "active": name in active_users,
                    "nick": nick
                })

        results.sort(key=lambda x: (not x.get("active"), x.get("nick", "").casefold(), x.get("username", "").casefold()))
        return results[:50]


    # ================= START =================

    def start(self):

        while True:

            client, addr = self.server.accept()

            print(f"[+] Connection from {addr}")

            threading.Thread(
                target=self.handle_client,
                args=(client, addr),
                daemon=True
            ).start()


    # ================= CLIENT =================

    def handle_client(self, client, addr):

        buffer = ""
        ip = addr[0] if addr else "unknown"

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
            token = login.get("token", "")

            if action == "token_login":
                if not self._rate_limit(f"auth:token:{ip}", 5, 30):
                    self.send_auth(client, "error", "Too many attempts")
                    client.close()
                    return
                username = self._username_by_token(token)
                if not username:
                    self.send_auth(client, "error", "Invalid token")
                    client.close()
                    return
            elif not username:
                self.send_auth(client, "error", "Missing username")
                client.close()
                return

            if not self.username_re.fullmatch(username):
                self.send_auth(client, "error", "Invalid username")
                client.close()
                return

            if action not in ("login", "register", "reset_request", "token_login"):
                self.send_auth(client, "error", "Invalid action")
                client.close()
                return

            if action not in ("reset_request", "token_login") and not password:
                self.send_auth(client, "error", "Missing username or password")
                client.close()
                return

            # ---- AUTH ----

            if action in ("login", "register", "reset_request"):
                if not self._rate_limit(f"auth:{action}:{ip}", 10, 60):
                    self.send_auth(client, "error", "Too many attempts")
                    client.close()
                    return

            if action == "register":

                if not email or (not is_console_email(email) and not self.email_re.fullmatch(email)):
                    self.send_auth(client, "error", "Invalid email")
                    client.close()
                    return

                if self._email_in_use(email):
                    self.send_auth(client, "error", "Email already in use")
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
                    if not self._rate_limit(f"register_verify:{ip}", 10, 60):
                        self.send_auth(client, "error", "Too many attempts")
                        continue
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

            elif action == "reset_request":

                if not email or (not is_console_email(email) and not self.email_re.fullmatch(email)):
                    self.send_auth(client, "error", "Invalid email")
                    client.close()
                    return

                with self.accounts_lock:
                    stored = self.accounts.get(username)

                if not stored:
                    self.send_auth(client, "error", "User not found")
                    client.close()
                    return

                stored_email = (stored.get("email") or "").strip().lower()
                if stored_email != email.lower():
                    self.send_auth(client, "error", "Email mismatch")
                    client.close()
                    return

                code = CodeGenerator.generate_code(6)
                expires_at = time.time() + 300

                with self.reset_pending_lock:
                    self.reset_pending[username] = {
                        "email": email,
                        "code": code,
                        "expires_at": expires_at
                    }

                self.mailer.send_code(email, code)
                self.send_auth(client, "need_code", "Code sent to email")

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
                    if not self._rate_limit(f"reset:{ip}", 10, 60):
                        self.send_auth(client, "error", "Too many attempts")
                        continue
                    action2 = verify.get("action")
                    if action2 not in ("reset_check", "reset_verify"):
                        self.send_auth(client, "error", "Invalid action")
                        client.close()
                        return

                    v_user = verify.get("username", "").strip()
                    v_code = verify.get("code", "").strip()

                    with self.reset_pending_lock:
                        pending = self.reset_pending.get(v_user)

                        if not pending:
                            self.send_auth(client, "error", "No pending code")
                            continue

                        if time.time() > pending["expires_at"]:
                            del self.reset_pending[v_user]
                            self.send_auth(client, "error", "Code expired")
                            continue

                        if v_code and v_code != pending["code"]:
                            self.send_auth(client, "error", "Wrong code")
                            continue

                        if action2 == "reset_check":
                            pending["verified"] = True
                            self.reset_pending[v_user] = pending
                            self.send_auth(client, "ok", "Code verified")
                            continue

                        if not v_code and not pending.get("verified"):
                            self.send_auth(client, "error", "Wrong code")
                            continue

                        del self.reset_pending[v_user]

                    new_password = verify.get("password", "")
                    if not new_password:
                        self.send_auth(client, "error", "Missing password")
                        continue

                    salt = secrets.token_hex(16)
                    pw_hash = self._hash_password(new_password, salt)

                    with self.accounts_lock:
                        acc = self.accounts.get(v_user)
                        if not acc:
                            self.send_auth(client, "error", "User not found")
                            client.close()
                            return
                        acc["salt"] = salt
                        acc["hash"] = pw_hash
                        self.accounts[v_user] = acc
                        self.save_accounts()

                    self.send_auth(client, "ok", "Password reset")
                    client.close()
                    return

            elif action == "login":
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
            else:
                # token_login already validated
                pass

            # ---- REGISTER CLIENT ----

            with self.clients_lock:

                if username in self.clients.values():

                    self.send_auth(client, "error", "Username already taken")
                    client.close()
                    return

                self.clients[client] = username

            token_value = self._issue_token(username)
            self.send_auth(client, "ok", token=token_value, username=username)

            print(f"[+] {username} logged in")

            try:
                session_payload = {
                    "type": "session",
                    "username": username
                }
                client.send((json.dumps(session_payload) + "\n").encode())
            except:
                pass

            self.send_history(client)
            self.send_dm_history(client, username)
            self.send_pending_receipts(client, username)
            self.send_contacts(client, username)

            self.send_system(f"{username} joined the chat")
            self.send_all_contacts()

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
                                        if len(avatar) <= 250_000:
                                            profile["avatar"] = avatar
                                    acc["profile"] = profile
                                    self.accounts[username] = acc
                                    self.save_accounts()
                            self.send_all_contacts()
                            continue
                        if action == "search_users":
                            query = (msg.get("query") or "").strip()
                            results = self._search_users(query, requester=username)
                            payload = {
                                "type": "search_results",
                                "query": query,
                                "results": results
                            }
                            try:
                                client.send((json.dumps(payload) + "\n").encode())
                            except:
                                pass
                            continue
                        if action == "add_contact":
                            target = (msg.get("username") or "").strip()
                            if target and target != username:
                                self._add_contact_pair(username, target)
                                sender_sock = self._socket_by_username(username)
                                target_sock = self._socket_by_username(target)
                                if sender_sock:
                                    self.send_contacts(sender_sock, username)
                                if target_sock:
                                    self.send_contacts(target_sock, target)
                            continue
                        if action == "remove_contact":
                            target = (msg.get("username") or "").strip()
                            if target and target != username:
                                self._remove_contact_pair(username, target)
                                sender_sock = self._socket_by_username(username)
                                target_sock = self._socket_by_username(target)
                                if sender_sock:
                                    self.send_contacts(sender_sock, username)
                                if target_sock:
                                    self.send_contacts(target_sock, target)
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

        self._add_contact_pair(sender, target)
        sender_sock = self._socket_by_username(sender)
        target_sock = self._socket_by_username(target)
        if sender_sock:
            self.send_contacts(sender_sock, sender)
        if target_sock:
            self.send_contacts(target_sock, target)

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
        self.send_all_contacts()


# ================= MAIN =================


def main():

    server = MAXutkoServer(port=5555)

    server.start()


if __name__ == "__main__":
    main()
