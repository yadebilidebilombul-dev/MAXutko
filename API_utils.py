import json
import os
import re
import secrets
import threading
import time


class BotAPI:
    def __init__(self, server, deactivated_message=""):
        self.server = server
        self.deactivated_message = deactivated_message
        self._init_storage()

    def _init_storage(self):
        self.server.bots_path = os.path.join(os.path.dirname(__file__), "server", "bots.json")
        self.server.bots_lock = self.server.bots_lock if hasattr(self.server, "bots_lock") else None
        if self.server.bots_lock is None:
            self.server.bots_lock = threading.Lock()
        self.server.bots = self.load_bots()

    def load_bots(self):
        if not os.path.exists(self.server.bots_path):
            return {}
        try:
            with open(self.server.bots_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                return data
        except Exception:
            pass
        return {}

    def save_bots(self):
        with open(self.server.bots_path, "w", encoding="utf-8") as f:
            json.dump(self.server.bots, f, ensure_ascii=False, indent=2)

    def _bot_by_token(self, token):
        token = (token or "").strip()
        if not token:
            return "", None
        with self.server.bots_lock:
            for name, bot in self.server.bots.items():
                if isinstance(bot, dict) and (bot.get("token") or "").strip() == token:
                    return name, dict(bot)
        return "", None

    def _send_bot_response(self, client, action, status, message="", **extra):
        data = {
            "type": action,
            "status": status
        }
        if message:
            data["message"] = message
        if extra:
            data.update(extra)
        self.server._safe_send_json(client, data)

    def handle_request(self, client, ip, payload):
        action = (payload.get("action") or "").strip()
        if action == "bot_create":
            self._handle_bot_create(client, ip, payload)
            return
        if action == "bot_send":
            self._handle_bot_send(client, ip, payload)
            return
        if action == "bot_fetch":
            self._handle_bot_fetch(client, ip, payload)
            return
        self._send_bot_response(client, action or "bot", "error", "\u041d\u0435\u0438\u0437\u0432\u0435\u0441\u0442\u043d\u043e\u0435 \u0434\u0435\u0439\u0441\u0442\u0432\u0438\u0435")

    def _handle_bot_create(self, client, ip, payload):
        if not self.server._rate_limit(f"bot:create:{ip}", 5, 60):
            self._send_bot_response(client, "bot_create", "error", "\u0421\u043b\u0438\u0448\u043a\u043e\u043c \u043c\u043d\u043e\u0433\u043e \u043f\u043e\u043f\u044b\u0442\u043e\u043a")
            return

        passcode = (
            payload.get("passcode")
            or payload.get("passcode_hash")
            or payload.get("PassCodeHash")
            or ""
        )
        passcode = passcode.strip()
        if not passcode:
            self._send_bot_response(client, "bot_create", "error", "\u041d\u0435 \u0443\u043a\u0430\u0437\u0430\u043d PassCodeHash")
            return

        owner = self.server._find_username_by_passcode(passcode)
        if not owner:
            self._send_bot_response(client, "bot_create", "error", "\u041d\u0435\u0432\u0435\u0440\u043d\u044b\u0439 PassCodeHash")
            return
        if self.server._is_account_blocked(owner):
            self._send_bot_response(client, "bot_create", "error", self.deactivated_message)
            return

        bot_name = (payload.get("bot_name") or payload.get("name") or "").strip()
        if not bot_name:
            self._send_bot_response(client, "bot_create", "error", "\u041d\u0435 \u0443\u043a\u0430\u0437\u0430\u043d\u043e \u0438\u043c\u044f \u0431\u043e\u0442\u0430")
            return
        if not self.server.username_re.fullmatch(bot_name):
            self._send_bot_response(client, "bot_create", "error", "\u041d\u0435\u043a\u043e\u0440\u0440\u0435\u043a\u0442\u043d\u043e\u0435 \u0438\u043c\u044f \u0431\u043e\u0442\u0430")
            return
        if not bot_name.lower().endswith("_bot"):
            self._send_bot_response(client, "bot_create", "error", "\u0418\u043c\u044f \u0431\u043e\u0442\u0430 \u0434\u043e\u043b\u0436\u043d\u043e \u0437\u0430\u043a\u0430\u043d\u0447\u0438\u0432\u0430\u0442\u044c\u0441\u044f \u043d\u0430 _bot")
            return

        bot_username = (payload.get("bot_username") or payload.get("username") or "").strip()
        if bot_username:
            bot_username = bot_username[:self.server.max_profile_nick_size]

        recovery_password = (payload.get("recovery_password") or payload.get("recovery") or "").strip()
        if not re.fullmatch(r"\d{6}", recovery_password):
            self._send_bot_response(client, "bot_create", "error", "\u041f\u0430\u0440\u043e\u043b\u044c \u0432\u043e\u0441\u0441\u0442\u0430\u043d\u043e\u0432\u043b\u0435\u043d\u0438\u044f \u0434\u043e\u043b\u0436\u0435\u043d \u0431\u044b\u0442\u044c 6 \u0446\u0438\u0444\u0440")
            return

        with self.server.accounts_lock:
            if bot_name in self.server.accounts:
                self._send_bot_response(client, "bot_create", "error", "\u0418\u043c\u044f \u0443\u0436\u0435 \u0437\u0430\u043d\u044f\u0442\u043e")
                return
        with self.server.bots_lock:
            if bot_name in self.server.bots:
                self._send_bot_response(client, "bot_create", "error", "\u0411\u043e\u0442 \u0443\u0436\u0435 \u0441\u0443\u0449\u0435\u0441\u0442\u0432\u0443\u0435\u0442")
                return

        profile = {
            "nick": bot_username,
            "about": "",
            "avatar": "",
            "clan_primary": "",
            "clan_secondary": "\u041d\u043e\u0432\u0438\u0447\u043e\u043a"
        }
        with self.server.accounts_lock:
            self.server.accounts[bot_name] = {
                "salt": "",
                "hash": "",
                "email": "",
                "passcode_hash": "",
                "profile": profile,
                "registered_at": int(time.time())
            }
            self.server.save_accounts()

        token = secrets.token_hex(24)
        with self.server.bots_lock:
            existing_tokens = {
                (entry.get("token") or "").strip()
                for entry in self.server.bots.values()
                if isinstance(entry, dict)
            }
            while token in existing_tokens:
                token = secrets.token_hex(24)
            self.server.bots[bot_name] = {
                "owner": owner,
                "bot_username": bot_username,
                "recovery_password": recovery_password,
                "token": token,
                "created_at": int(time.time()),
                "last_seen_id": ""
            }
            self.save_bots()

        self.server._safe_log("bot_create", {
            "owner": owner,
            "bot": bot_name,
            "ip": ip
        })
        self._send_bot_response(client, "bot_create", "ok", token=token)

    def _handle_bot_send(self, client, ip, payload):
        if not self.server._rate_limit(f"bot:send:{ip}", 60, 60):
            self._send_bot_response(client, "bot_send", "error", "\u0421\u043b\u0438\u0448\u043a\u043e\u043c \u043c\u043d\u043e\u0433\u043e \u0437\u0430\u043f\u0440\u043e\u0441\u043e\u0432")
            return

        token = (payload.get("token") or payload.get("bot_token") or "").strip()
        if not token:
            self._send_bot_response(client, "bot_send", "error", "\u041d\u0435 \u0443\u043a\u0430\u0437\u0430\u043d \u0442\u043e\u043a\u0435\u043d")
            return

        bot_name, _bot = self._bot_by_token(token)
        if not bot_name:
            self._send_bot_response(client, "bot_send", "error", "\u041d\u0435\u0432\u0435\u0440\u043d\u044b\u0439 \u0442\u043e\u043a\u0435\u043d")
            return

        if not self.server._rate_limit(f"bot:send:{bot_name}", 120, 60):
            self._send_bot_response(client, "bot_send", "error", "\u0421\u043b\u0438\u0448\u043a\u043e\u043c \u043c\u043d\u043e\u0433\u043e \u0437\u0430\u043f\u0440\u043e\u0441\u043e\u0432")
            return

        target = (payload.get("to") or "").strip()
        text = payload.get("message", "")
        reply_to = payload.get("reply_to")
        if not target:
            self._send_bot_response(client, "bot_send", "error", "\u041d\u0435 \u0443\u043a\u0430\u0437\u0430\u043d \u043f\u043e\u043b\u0443\u0447\u0430\u0442\u0435\u043b\u044c")
            return
        if not isinstance(text, str) or not text.strip():
            self._send_bot_response(client, "bot_send", "error", "\u041f\u0443\u0441\u0442\u043e\u0435 \u0441\u043e\u043e\u0431\u0449\u0435\u043d\u0438\u0435")
            return
        if len(text) > self.server.max_message_size:
            self._send_bot_response(client, "bot_send", "error", "\u0421\u043b\u0438\u0448\u043a\u043e\u043c \u0434\u043b\u0438\u043d\u043d\u043e\u0435 \u0441\u043e\u043e\u0431\u0449\u0435\u043d\u0438\u0435")
            return
        if not self.server._user_exists(target):
            self._send_bot_response(client, "bot_send", "error", "\u041f\u043e\u043b\u044c\u0437\u043e\u0432\u0430\u0442\u0435\u043b\u044c \u043d\u0435 \u043d\u0430\u0439\u0434\u0435\u043d")
            return

        self.server.send_direct_message(bot_name, target, text, reply_to)
        self._send_bot_response(client, "bot_send", "ok")

    def _collect_bot_messages(self, bot_name, last_seen_id):
        items = []
        to_notify = []
        new_last = last_seen_id
        changed = False
        with self.server.dm_history_lock:
            start_index = 0
            if last_seen_id:
                for idx, msg in enumerate(self.server.dm_history):
                    if msg.get("id") == last_seen_id:
                        start_index = idx + 1
                        break
            for msg in self.server.dm_history[start_index:]:
                if msg.get("type") not in ("dm", "file"):
                    continue
                if msg.get("to") != bot_name:
                    continue
                if not msg.get("delivered", False):
                    msg["delivered"] = True
                    changed = True
                    to_notify.append({
                        "sender": msg.get("username"),
                        "id": msg.get("id"),
                        "delivered": True,
                        "read": bool(msg.get("read", False))
                    })
                items.append(dict(msg))
            if changed:
                self.server.save_dm_history()
        if items:
            new_last = items[-1].get("id", last_seen_id)
        return items, new_last, to_notify

    def _handle_bot_fetch(self, client, ip, payload):
        if not self.server._rate_limit(f"bot:fetch:{ip}", 60, 60):
            self._send_bot_response(client, "bot_fetch", "error", "\u0421\u043b\u0438\u0448\u043a\u043e\u043c \u043c\u043d\u043e\u0433\u043e \u0437\u0430\u043f\u0440\u043e\u0441\u043e\u0432")
            return

        token = (payload.get("token") or payload.get("bot_token") or "").strip()
        if not token:
            self._send_bot_response(client, "bot_fetch", "error", "\u041d\u0435 \u0443\u043a\u0430\u0437\u0430\u043d \u0442\u043e\u043a\u0435\u043d")
            return

        command = (payload.get("command") or payload.get("text") or "").strip()
        if command.casefold() != "\u0432\u044b\u0448\u043b\u0438 \u0432\u0441\u0435 \u043d\u0430\u043a\u043e\u043f\u0438\u0432\u0448\u0438\u0435\u0441\u044f".casefold():
            self._send_bot_response(client, "bot_fetch", "error", "\u041d\u0435\u0432\u0435\u0440\u043d\u0430\u044f \u043a\u043e\u043c\u0430\u043d\u0434\u0430")
            return

        bot_name, bot = self._bot_by_token(token)
        if not bot_name:
            self._send_bot_response(client, "bot_fetch", "error", "\u041d\u0435\u0432\u0435\u0440\u043d\u044b\u0439 \u0442\u043e\u043a\u0435\u043d")
            return

        last_seen_id = (bot.get("last_seen_id") or "").strip() if isinstance(bot, dict) else ""
        items, new_last, to_notify = self._collect_bot_messages(bot_name, last_seen_id)

        if new_last and new_last != last_seen_id:
            with self.server.bots_lock:
                entry = self.server.bots.get(bot_name, {})
                if isinstance(entry, dict):
                    entry["last_seen_id"] = new_last
                    self.server.bots[bot_name] = entry
                    self.save_bots()

        self._send_bot_response(client, "bot_fetch", "ok", items=items)

        for item in to_notify:
            sender = item.get("sender")
            if sender:
                self.server.send_dm_receipt(sender, item.get("id"), item.get("delivered"), item.get("read"))
