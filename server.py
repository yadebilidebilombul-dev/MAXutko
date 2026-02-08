import socket
import threading
import json
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

            username = login.get("username")

            if not username:
                client.close()
                return


            # ---- REGISTER CLIENT ----

            with self.clients_lock:

                if username in self.clients.values():

                    client.send(
                        (json.dumps({
                            "type": "system",
                            "message": "Username already taken"
                        }) + "\n").encode()
                    )

                    client.close()
                    return

                self.clients[client] = username


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
