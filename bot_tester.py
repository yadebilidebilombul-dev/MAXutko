import json
import os
import socket
import time


HOST = os.environ.get("MAXUTKO_HOST", "26.202.145.247")
PORT = int(os.environ.get("MAXUTKO_PORT", "5555"))

PASSCODE_HASH = os.environ.get("MAXUTKO_PASSCODE", "52e45145d931670a6949bc2962bb2d9432dd5db6d9686aa227113c8cee691298031efdccb8c8c46cac68e9c2c8e2ed1c9c8991675811ba29900c2283e7a45e5c")
BOT_NAME = os.environ.get("MAXUTKO_BOT_NAME", "ping_bot")
BOT_USERNAME = os.environ.get("MAXUTKO_BOT_USERNAME", "")
RECOVERY_PASSWORD = os.environ.get("MAXUTKO_BOT_RECOVERY", "123456")

BOT_TOKEN = os.environ.get("MAXUTKO_BOT_TOKEN", "4328b65b06575b70f620e80c3a53b88db7554cd1741ca2f4")
POLL_SECONDS = float(os.environ.get("MAXUTKO_BOT_POLL", "3"))


def _read_line(sock):
    buf = ""
    while "\n" not in buf:
        data = sock.recv(4096)
        if not data:
            break
        buf += data.decode("utf-8", errors="ignore")
    if "\n" in buf:
        line, _ = buf.split("\n", 1)
        return line
    return buf.strip()


def send_request(payload):
    with socket.create_connection((HOST, PORT), timeout=10) as sock:
        data = json.dumps(payload, ensure_ascii=False) + "\n"
        sock.sendall(data.encode("utf-8"))
        line = _read_line(sock)
        if not line:
            return {}
        try:
            return json.loads(line)
        except Exception:
            return {}


def create_bot():
    if not PASSCODE_HASH:
        raise RuntimeError("Set MAXUTKO_PASSCODE or PASSCODE_HASH")
    payload = {
        "action": "bot_create",
        "PassCodeHash": PASSCODE_HASH,
        "bot_name": BOT_NAME,
        "bot_username": BOT_USERNAME,
        "recovery_password": RECOVERY_PASSWORD
    }
    resp = send_request(payload)
    if resp.get("status") != "ok":
        raise RuntimeError(f"bot_create failed: {resp.get('message')}")
    return resp.get("token", "")


def fetch_messages(token):
    payload = {
        "action": "bot_fetch",
        "token": token,
        "command": "вышли все накопившиеся"
    }
    return send_request(payload)


def send_message(token, to_user, text):
    payload = {
        "action": "bot_send",
        "token": token,
        "to": to_user,
        "message": text
    }
    return send_request(payload)


def main():
    token = BOT_TOKEN
    if not token:
        raise RuntimeError("No bot token")

    print(f"[+] Bot token: {token}")
    print("[+] Waiting for messages. Replying 'pong' to 'ping'.")

    while True:
        resp = fetch_messages(token)
        if resp.get("status") == "ok":
            items = resp.get("items") or []
            for item in items:
                msg = (item.get("message") or "").strip().casefold()
                sender = (item.get("username") or "").strip()
                if msg == "ping" and sender:
                    send_message(token, sender, "pong")
        time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    main()
