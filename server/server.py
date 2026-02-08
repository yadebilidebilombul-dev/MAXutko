import socket
import threading
import json
import os
import re
import time
import hashlib
import secrets
import sys
import base64
from datetime import datetime

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.append(BASE_DIR)

from mailSystem.mailCla import MailSender, CodeGenerator, is_console_email


class MAXutkoServer:

    def __init__(self, host="26.202.145.247", port=5555):

        self.host = host
        self.port = port

        self.server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

        self.server.bind((self.host, self.port))
        self.server.listen(100)

        print(f"[+] Server started on {self.host}:{self.port}")

        self.clients = {}        # socket -> username
        self.clients_lock = threading.Lock()

        self.clans = ["Одмен", "Новичок", "Старик", "Старейшина", "Модер"]
        self.primary_clans = {"Одмен", "Модер"}
        self.secondary_clans = {"Новичок", "Старик", "Старейшина"}
        self.clan_six_months_seconds = 6 * 30 * 24 * 60 * 60
        self.clan_year_seconds = 365 * 24 * 60 * 60

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

        self.files_dir = os.path.join(os.path.dirname(__file__), "files")
        os.makedirs(self.files_dir, exist_ok=True)
        self.file_index_path = os.path.join(os.path.dirname(__file__), "files_index.json")
        self.file_index_lock = threading.Lock()
        self.file_index = self.load_file_index()
        self.file_uploads = {}
        self.file_uploads_lock = threading.Lock()
        self.max_file_size = 100 * 1024 * 1024

        self.send_locks = {}
        self.send_locks_lock = threading.Lock()


    # ================= ACCOUNTS =================

    def load_accounts(self):

        if not os.path.exists(self.accounts_path):
            return {}

        try:
            with open(self.accounts_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                changed = False
                for name, item in data.items():
                    normalized, updated = self._normalize_account(item)
                    data[name] = normalized
                    if updated:
                        changed = True
                if changed:
                    try:
                        with open(self.accounts_path, "w", encoding="utf-8") as fw:
                            json.dump(data, fw, ensure_ascii=False, indent=2)
                    except Exception:
                        pass
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
                "profile": {"nick": "", "about": "", "avatar": "", "clan_primary": "", "clan_secondary": "Новичок"},
                "registered_at": int(time.time())
            }, True
        changed = False
        profile = data.get("profile")
        if not isinstance(profile, dict):
            profile = {}
            changed = True
        profile.setdefault("nick", "")
        profile.setdefault("about", "")
        profile.setdefault("avatar", "")
        if "clan_primary" not in profile:
            profile["clan_primary"] = ""
            changed = True
        if "clan_secondary" not in profile:
            profile["clan_secondary"] = "Новичок"
            changed = True
        if "clan" in profile:
            legacy = (profile.get("clan") or "").strip()
            if legacy in self.primary_clans and not profile.get("clan_primary"):
                profile["clan_primary"] = legacy
                changed = True
            if legacy in self.secondary_clans and not profile.get("clan_secondary"):
                profile["clan_secondary"] = legacy
                changed = True
            profile.pop("clan", None)
            changed = True
        data["profile"] = profile
        data.setdefault("salt", "")
        data.setdefault("hash", "")
        data.setdefault("email", "")
        if not isinstance(data.get("registered_at"), (int, float)) or data.get("registered_at", 0) <= 0:
            data["registered_at"] = int(time.time())
            changed = True
        clan_changed = self._refresh_account_clan(data)
        return data, changed or clan_changed

    def _auto_clan_for_account(self, registered_at):
        now = time.time()
        age = max(0, now - registered_at)
        if age >= self.clan_year_seconds:
            return "Старейшина"
        if age >= self.clan_six_months_seconds:
            return "Старик"
        return "Новичок"

    def _get_account_clan(self, acc):
        profile = acc.get("profile") or {}
        clan = (profile.get("clan_primary") or "").strip()
        if clan in self.primary_clans:
            return clan
        return ""

    def _refresh_account_clan(self, acc):
        if not isinstance(acc, dict):
            return False
        profile = acc.get("profile")
        if not isinstance(profile, dict):
            profile = {}
        if "registered_at" not in acc or not isinstance(acc.get("registered_at"), (int, float)):
            acc["registered_at"] = int(time.time())
        current_secondary = (profile.get("clan_secondary") or "").strip()
        if current_secondary not in self.secondary_clans:
            current_secondary = ""
        auto = self._auto_clan_for_account(acc.get("registered_at", int(time.time())))
        if profile.get("clan_secondary") != auto:
            profile["clan_secondary"] = auto
            acc["profile"] = profile
            return True
        acc["profile"] = profile
        return False

    def _refresh_account_clan_for_user(self, username):
        if not username:
            return None
        with self.accounts_lock:
            acc = self.accounts.get(username)
            if not acc:
                return None
            changed = self._refresh_account_clan(acc)
            self.accounts[username] = acc
            if changed:
                self.save_accounts()
            return acc


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


    # ================= FILES =================

    def load_file_index(self):
        if not os.path.exists(self.file_index_path):
            return {}
        try:
            with open(self.file_index_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                cleaned = {}
                for fid, item in data.items():
                    if not isinstance(item, dict):
                        continue
                    path = item.get("path")
                    if not path or not os.path.exists(path):
                        continue
                    cleaned[fid] = item
                return cleaned
        except Exception:
            pass
        return {}

    def save_file_index(self):
        with open(self.file_index_path, "w", encoding="utf-8") as f:
            json.dump(self.file_index, f, ensure_ascii=False, indent=2)

    def _sanitize_filename(self, name):
        base = os.path.basename(name or "").strip()
        base = re.sub(r"[^\w\-. ()\[\]]+", "_", base, flags=re.UNICODE)
        if not base:
            base = "file"
        return base[:180]

    def _safe_send(self, sock, data):
        if sock is None:
            return False
        try:
            if isinstance(data, str):
                data = data.encode()
            with self.send_locks_lock:
                lock = self.send_locks.get(sock)
            if lock:
                with lock:
                    sock.sendall(data)
            else:
                sock.sendall(data)
            return True
        except Exception:
            return False

    def _safe_send_json(self, sock, payload):
        try:
            return self._safe_send(sock, json.dumps(payload) + "\n")
        except Exception:
            return False


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
            "profile": {"nick": "", "about": "", "avatar": "", "clan_primary": "", "clan_secondary": "Новичок"},
            "registered_at": int(time.time())
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
        self._safe_send_json(client, data)

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
                changed = False
                for item in data:
                    if not isinstance(item, dict):
                        continue
                    if item.get("type") not in ("message", "file"):
                        continue
                    if not item.get("id"):
                        item["id"] = secrets.token_hex(12)
                        changed = True
                if changed:
                    try:
                        with open(self.history_path, "w", encoding="utf-8") as fw:
                            json.dump(data, fw, ensure_ascii=False, indent=2)
                    except Exception:
                        pass
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

        self._safe_send_json(client, payload)

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
        self._safe_send_json(target_client, payload)

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
                if msg.get("type") not in ("dm", "file"):
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
        self._safe_send_json(client, payload)

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
            if self._safe_send_json(target_sock, payload):
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
            self._safe_send_json(client, payload)
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
        if target_client is not None:
            self._safe_send_json(target_client, payload)
            return

        with self.clients_lock:
            for c in list(self.clients.keys()):
                self._safe_send_json(c, payload)

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
                            "profile": {"nick": "", "about": "", "avatar": "", "clan_primary": "", "clan_secondary": "Новичок"},
                            "registered_at": int(time.time())
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

            self._refresh_account_clan_for_user(username)

            # ---- REGISTER CLIENT ----

            with self.clients_lock:

                if username in self.clients.values():

                    self.send_auth(client, "error", "Username already taken")
                    client.close()
                    return

                self.clients[client] = username
            with self.send_locks_lock:
                self.send_locks[client] = threading.Lock()

            token_value = self._issue_token(username)
            self.send_auth(client, "ok", token=token_value, username=username)

            print(f"[+] {username} logged in")

            try:
                session_payload = {
                    "type": "session",
                    "username": username
                }
                self._safe_send_json(client, session_payload)
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
                        if action == "file_upload_start":
                            self._handle_file_upload_start(username, msg, client)
                            continue
                        if action == "file_upload_chunk":
                            self._handle_file_upload_chunk(username, msg, client)
                            continue
                        if action == "file_upload_end":
                            self._handle_file_upload_end(username, msg, client)
                            continue
                        if action == "file_upload_cancel":
                            self._handle_file_upload_cancel(username, msg, client)
                            continue
                        if action == "file_request":
                            self._handle_file_request(username, msg, client)
                            continue
                        if action == "get_profile":
                            target = (msg.get("username") or "").strip()
                            acc = self._refresh_account_clan_for_user(target)
                            if acc:
                                profile = dict(acc.get("profile") or {})
                            else:
                                profile = {"nick": "", "about": "", "avatar": "", "clan_primary": "", "clan_secondary": "Новичок"}
                            payload = {
                                "type": "profile",
                                "username": target,
                                "profile": profile
                            }
                            self._safe_send_json(client, payload)
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
                            self._safe_send_json(client, payload)
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
                            self._safe_send_json(client, payload)
                            continue
                        if action == "set_note":
                            target = (msg.get("username") or "").strip()
                            note = msg.get("note")
                            if target and isinstance(note, str):
                                self.set_note(username, target, note[:1000])
                            continue
                        if action == "set_clan":
                            target = (msg.get("username") or "").strip()
                            new_clan = (msg.get("clan") or "").strip()
                            level = (msg.get("level") or "").strip().lower()
                            if not target or not new_clan:
                                continue
                            requester = self._refresh_account_clan_for_user(username)
                            if not requester:
                                continue
                            if self._get_account_clan(requester) != "Одмен":
                                continue
                            if level not in ("primary", "secondary"):
                                level = "secondary"
                            if len(new_clan) > 32:
                                new_clan = new_clan[:32]
                            with self.accounts_lock:
                                acc = self.accounts.get(target)
                                if not acc:
                                    continue
                                profile = dict(acc.get("profile") or {})
                                if level == "primary":
                                    profile["clan_primary"] = new_clan
                                else:
                                    profile["clan_secondary"] = new_clan
                                acc["profile"] = profile
                                self.accounts[target] = acc
                                self.save_accounts()
                            continue

                    text = msg.get("message")
                    target = msg.get("to", "").strip()
                    reply_to = msg.get("reply_to")

                    if not text:
                        continue

                    if target:
                        self.send_direct_message(username, target, text, reply_to)
                    else:
                        self.broadcast_message(username, text, reply_to)

        except Exception as e:

            print("Client error:", e)

        finally:

            self.remove_client(client)


    # ================= BROADCAST =================

    def broadcast_message(self, username, text, reply_to=None):

        msg_id = secrets.token_hex(12)
        data = {
            "id": msg_id,
            "username": username,
            "message": text,
            "timestamp": datetime.now().strftime("%H:%M:%S"),
            "type": "message"
        }
        if reply_to:
            data["reply_to"] = reply_to

        self.append_history(data)

        with self.clients_lock:
            for c in list(self.clients.keys()):
                self._safe_send_json(c, data)


    def _socket_by_username(self, username):

        with self.clients_lock:
            for sock, name in self.clients.items():
                if name == username:
                    return sock
        return None


    def send_direct_message(self, sender, target, text, reply_to=None):

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
        if reply_to:
            data["reply_to"] = reply_to

        self.append_dm_history(dict(data))

        if target_sock:
            self._safe_send_json(target_sock, data)
        if sender_sock:
            self._safe_send_json(sender_sock, data)


    # ================= FILE TRANSFER =================

    def _build_file_payload(self, sender, target, file_id, filename, size, reply_to=None):
        msg_id = secrets.token_hex(12)
        data = {
            "id": msg_id,
            "username": sender,
            "timestamp": datetime.now().strftime("%H:%M:%S"),
            "type": "file",
            "file_id": file_id,
            "filename": filename,
            "size": size
        }
        if reply_to:
            data["reply_to"] = reply_to
        if target:
            data["to"] = target
            data["delivered"] = self._socket_by_username(target) is not None
            data["read"] = False
        return data

    def send_direct_file(self, sender, target, file_id, filename, size, reply_to=None):
        self._add_contact_pair(sender, target)
        sender_sock = self._socket_by_username(sender)
        target_sock = self._socket_by_username(target)
        if sender_sock:
            self.send_contacts(sender_sock, sender)
        if target_sock:
            self.send_contacts(target_sock, target)

        data = self._build_file_payload(sender, target, file_id, filename, size, reply_to)
        self.append_dm_history(dict(data))

        if target_sock:
            self._safe_send_json(target_sock, data)
        if sender_sock:
            self._safe_send_json(sender_sock, data)

    def broadcast_file(self, sender, file_id, filename, size, reply_to=None):
        data = self._build_file_payload(sender, "", file_id, filename, size, reply_to)
        self.append_history(data)
        with self.clients_lock:
            for c in list(self.clients.keys()):
                self._safe_send_json(c, data)

    def _handle_file_upload_start(self, username, msg, client):
        file_id = (msg.get("file_id") or "").strip()
        filename = (msg.get("filename") or "").strip()
        try:
            size = int(msg.get("size") or 0)
        except Exception:
            size = 0
        target = (msg.get("to") or "").strip()
        reply_to = msg.get("reply_to")

        if not file_id or not filename or size <= 0:
            self._safe_send_json(client, {
                "type": "file_upload_reject",
                "file_id": file_id,
                "message": "Invalid file"
            })
            return
        if size > self.max_file_size:
            self._safe_send_json(client, {
                "type": "file_upload_reject",
                "file_id": file_id,
                "message": "File too large"
            })
            return
        if target and not self._user_exists(target):
            self._safe_send_json(client, {
                "type": "file_upload_reject",
                "file_id": file_id,
                "message": "User not found"
            })
            return

        safe_name = self._sanitize_filename(filename)
        file_path = os.path.join(self.files_dir, f"{file_id}_{safe_name}")

        try:
            fp = open(file_path, "wb")
        except Exception:
            self._safe_send_json(client, {
                "type": "file_upload_reject",
                "file_id": file_id,
                "message": "Failed to open file"
            })
            return

        with self.file_uploads_lock:
            if file_id in self.file_uploads:
                try:
                    fp.close()
                except Exception:
                    pass
                self._safe_send_json(client, {
                    "type": "file_upload_reject",
                    "file_id": file_id,
                    "message": "File already exists"
                })
                return
            self.file_uploads[file_id] = {
                "owner": username,
                "target": target,
                "filename": safe_name,
                "size": size,
                "received": 0,
                "path": file_path,
                "fp": fp,
                "reply_to": reply_to,
                "created_at": int(time.time())
            }

        self._safe_send_json(client, {"type": "file_upload_ready", "file_id": file_id})

    def _handle_file_upload_chunk(self, username, msg, client):
        file_id = (msg.get("file_id") or "").strip()
        data_b64 = msg.get("data")
        if not file_id or not data_b64:
            return
        with self.file_uploads_lock:
            state = self.file_uploads.get(file_id)
        if not state or state.get("owner") != username:
            return
        try:
            chunk = base64.b64decode(data_b64)
        except Exception:
            self._safe_send_json(client, {
                "type": "file_upload_reject",
                "file_id": file_id,
                "message": "Invalid chunk"
            })
            return
        try:
            state["fp"].write(chunk)
            state["received"] += len(chunk)
            if state["received"] > state["size"]:
                raise Exception("Size overflow")
        except Exception:
            self._safe_send_json(client, {
                "type": "file_upload_reject",
                "file_id": file_id,
                "message": "Failed to write"
            })
            self._cleanup_upload(file_id)
            return

    def _handle_file_upload_end(self, username, msg, client):
        file_id = (msg.get("file_id") or "").strip()
        if not file_id:
            return
        with self.file_uploads_lock:
            state = self.file_uploads.get(file_id)
        if not state or state.get("owner") != username:
            return
        if state.get("received", 0) != state.get("size", 0):
            self._safe_send_json(client, {
                "type": "file_upload_reject",
                "file_id": file_id,
                "message": "Size mismatch"
            })
            self._cleanup_upload(file_id)
            return

        try:
            state["fp"].close()
        except Exception:
            pass

        with self.file_uploads_lock:
            self.file_uploads.pop(file_id, None)

        meta = {
            "filename": state.get("filename"),
            "size": state.get("size"),
            "path": state.get("path"),
            "owner": username,
            "target": state.get("target"),
            "created_at": state.get("created_at"),
            "scope": "dm" if state.get("target") else "group"
        }
        with self.file_index_lock:
            self.file_index[file_id] = meta
            self.save_file_index()

        target = state.get("target") or ""
        if target:
            self.send_direct_file(username, target, file_id, meta["filename"], meta["size"], state.get("reply_to"))
        else:
            self.broadcast_file(username, file_id, meta["filename"], meta["size"], state.get("reply_to"))

        self._safe_send_json(client, {"type": "file_upload_done", "file_id": file_id})

    def _handle_file_upload_cancel(self, username, msg, client):
        file_id = (msg.get("file_id") or "").strip()
        if not file_id:
            return
        with self.file_uploads_lock:
            state = self.file_uploads.get(file_id)
        if not state or state.get("owner") != username:
            return
        self._cleanup_upload(file_id)
        self._safe_send_json(client, {"type": "file_upload_reject", "file_id": file_id, "message": "Canceled"})

    def _cleanup_upload(self, file_id):
        with self.file_uploads_lock:
            state = self.file_uploads.pop(file_id, None)
        if not state:
            return
        try:
            fp = state.get("fp")
            if fp:
                fp.close()
        except Exception:
            pass
        try:
            path = state.get("path")
            if path and os.path.exists(path):
                os.remove(path)
        except Exception:
            pass

    def _handle_file_request(self, username, msg, client):
        file_id = (msg.get("file_id") or "").strip()
        if not file_id:
            return
        with self.file_index_lock:
            meta = self.file_index.get(file_id)
        if not meta:
            self._safe_send_json(client, {
                "type": "file_error",
                "file_id": file_id,
                "message": "File not found"
            })
            return

        scope = meta.get("scope")
        if scope == "dm":
            if username not in {meta.get("owner"), meta.get("target")}:
                self._safe_send_json(client, {
                    "type": "file_error",
                    "file_id": file_id,
                    "message": "Not allowed"
                })
                return

        path = meta.get("path")
        if not path or not os.path.exists(path):
            self._safe_send_json(client, {
                "type": "file_error",
                "file_id": file_id,
                "message": "Missing file"
            })
            return

        threading.Thread(
            target=self._stream_file_to_client,
            args=(client, file_id, meta),
            daemon=True
        ).start()

    def _stream_file_to_client(self, client, file_id, meta):
        filename = meta.get("filename") or "file"
        size = int(meta.get("size") or 0)
        path = meta.get("path")
        if not self._safe_send_json(client, {
            "type": "file_info",
            "file_id": file_id,
            "filename": filename,
            "size": size
        }):
            return
        try:
            seq = 0
            with open(path, "rb") as f:
                while True:
                    chunk = f.read(32 * 1024)
                    if not chunk:
                        break
                    payload = {
                        "type": "file_chunk",
                        "file_id": file_id,
                        "seq": seq,
                        "data": base64.b64encode(chunk).decode("ascii")
                    }
                    if not self._safe_send_json(client, payload):
                        return
                    seq += 1
            self._safe_send_json(client, {"type": "file_end", "file_id": file_id, "ok": True})
        except Exception:
            self._safe_send_json(client, {"type": "file_end", "file_id": file_id, "ok": False})


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

        with self.clients_lock:
            for c in list(self.clients.keys()):
                self._safe_send_json(c, data)


    # ================= REMOVE =================

    def remove_client(self, client):

        with self.clients_lock:

            if client not in self.clients:
                return

            username = self.clients[client]

            del self.clients[client]
        with self.send_locks_lock:
            self.send_locks.pop(client, None)

        cleanup_ids = []
        with self.file_uploads_lock:
            for fid, state in list(self.file_uploads.items()):
                if state.get("owner") == username:
                    cleanup_ids.append(fid)
        for fid in cleanup_ids:
            self._cleanup_upload(fid)

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
