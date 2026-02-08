import tkinter as tk
from tkinter import ttk, scrolledtext, messagebox, font
import socket
import threading
import json
import re


class MAXutkoClient:

    def __init__(self, root):

        self.root = root
        self.root.title("MAXutko Messenger")
        self.root.geometry("800x600")
        self.root.configure(bg="#2c3e50")
        self.root.minsize(600, 500)

        # Socket
        self.client_socket = None
        self.running = False
        self.prebuffer = ""

        self.host = "127.0.0.1"
        self.port = 5555
        self.username = ""
        self.password = ""
        self.email = ""
        self.auth_mode = tk.StringVar(value="login")
        self.pending_socket = None

        self.colors = {
            'bg': '#2c3e50',
            'fg': '#ecf0f1',
            'accent': '#3498db',
            'secondary': '#34495e',
            'warning': '#e74c3c',
            'message_bg': '#34495e',
            'my_message_bg': '#2980b9',
            'system_bg': '#7f8c8d'
        }

        self.setup_ui()
        self.show_login_screen()


    # ================= UI =================

    def setup_ui(self):

        self.style = ttk.Style()
        self.style.theme_use('clam')

        self.main_frame = ttk.Frame(self.root)
        self.login_frame = None
        self.chat_frame = None

        self.title_font = font.Font(size=24, weight="bold")
        self.normal_font = font.Font(size=10)
        self.message_font = font.Font(size=11)
        self.timestamp_font = font.Font(size=8, slant="italic")
        self.status_font = font.Font(size=12, weight="bold")


    def show_login_screen(self, status_text=""):

        for w in self.main_frame.winfo_children():
            w.destroy()

        self.login_frame = tk.Frame(self.main_frame, bg=self.colors['bg'])
        self.login_frame.pack(expand=True, fill="both", padx=40, pady=40)

        title = tk.Label(
            self.login_frame,
            text="MAXutko Messenger",
            font=self.title_font,
            bg=self.colors['bg'],
            fg=self.colors['accent']
        )
        title.pack(pady=30)

        # Username

        u_frame = tk.Frame(self.login_frame, bg=self.colors['bg'])
        u_frame.pack(pady=10)

        tk.Label(
            u_frame,
            text="Username:",
            bg=self.colors['bg'],
            fg=self.colors['fg']
        ).pack(side="left", padx=5)

        self.username_entry = tk.Entry(
            u_frame,
            width=30,
            bg=self.colors['secondary'],
            fg=self.colors['fg'],
            insertbackground="white"
        )
        self.username_entry.pack(side="left")

        # Password

        p_frame = tk.Frame(self.login_frame, bg=self.colors['bg'])
        p_frame.pack(pady=10)

        tk.Label(
            p_frame,
            text="Password:",
            bg=self.colors['bg'],
            fg=self.colors['fg']
        ).pack(side="left", padx=5)

        self.password_entry = tk.Entry(
            p_frame,
            width=30,
            show="*",
            bg=self.colors['secondary'],
            fg=self.colors['fg'],
            insertbackground="white"
        )
        self.password_entry.pack(side="left")

        # Email (for register)

        self.email_frame = tk.Frame(self.login_frame, bg=self.colors['bg'])
        self.email_frame.pack(pady=10)

        tk.Label(
            self.email_frame,
            text="Email:",
            bg=self.colors['bg'],
            fg=self.colors['fg']
        ).pack(side="left", padx=5)

        self.email_entry = tk.Entry(
            self.email_frame,
            width=30,
            bg=self.colors['secondary'],
            fg=self.colors['fg'],
            insertbackground="white"
        )
        self.email_entry.pack(side="left")

        # Server

        s_frame = tk.Frame(self.login_frame, bg=self.colors['bg'])
        s_frame.pack(pady=10)

        tk.Label(
            s_frame,
            text="Server:",
            bg=self.colors['bg'],
            fg=self.colors['fg']
        ).pack(side="left", padx=5)

        self.server_entry = tk.Entry(
            s_frame,
            width=30,
            bg=self.colors['secondary'],
            fg=self.colors['fg'],
            insertbackground="white"
        )
        self.server_entry.pack(side="left")

        self.server_entry.insert(0, "127.0.0.1:5555")

        # Mode

        mode_frame = tk.Frame(self.login_frame, bg=self.colors['bg'])
        mode_frame.pack(pady=10)

        tk.Radiobutton(
            mode_frame,
            text="Login",
            variable=self.auth_mode,
            value="login",
            bg=self.colors['bg'],
            fg=self.colors['fg'],
            selectcolor=self.colors['secondary'],
            activebackground=self.colors['bg'],
            activeforeground=self.colors['fg']
        ).pack(side="left", padx=8)

        tk.Radiobutton(
            mode_frame,
            text="Register",
            variable=self.auth_mode,
            value="register",
            bg=self.colors['bg'],
            fg=self.colors['fg'],
            selectcolor=self.colors['secondary'],
            activebackground=self.colors['bg'],
            activeforeground=self.colors['fg']
        ).pack(side="left", padx=8)

        # Button

        btn = tk.Button(
            self.login_frame,
            text="Login",
            command=self.connect_to_server,
            bg=self.colors['accent'],
            fg="white",
            padx=20,
            pady=8
        )
        btn.pack(pady=25)
        self.connect_btn = btn

        # Status

        self.status_label = tk.Label(
            self.login_frame,
            text=status_text,
            bg=self.colors['bg'],
            fg=self.colors['accent'],
            font=self.status_font
        )
        self.status_label.pack(pady=5)

        # Code verify

        self.code_frame = tk.Frame(self.login_frame, bg=self.colors['bg'])

        tk.Label(
            self.code_frame,
            text="Code:",
            bg=self.colors['bg'],
            fg=self.colors['fg']
        ).pack(side="left", padx=5)

        self.code_entry = tk.Entry(
            self.code_frame,
            width=20,
            bg=self.colors['secondary'],
            fg=self.colors['fg'],
            insertbackground="white"
        )
        self.code_entry.pack(side="left")

        self.verify_btn = tk.Button(
            self.code_frame,
            text="Verify",
            command=self.verify_code,
            bg=self.colors['accent'],
            fg="white",
            padx=16,
            pady=6
        )
        self.verify_btn.pack(side="left", padx=10)

        def _sync_ui(*_):
            is_register = self.auth_mode.get() == "register"
            self.connect_btn.config(text="Register" if is_register else "Login")
            if is_register:
                self.email_frame.pack(pady=10)
            else:
                self.email_frame.pack_forget()
            self.code_frame.pack_forget()

        self.auth_mode.trace_add("write", _sync_ui)
        _sync_ui()

        self.main_frame.pack(expand=True, fill="both")


    # ================= CONNECT =================

    def connect_to_server(self):

        self.username = self.username_entry.get().strip()
        self.password = self.password_entry.get().strip()
        self.email = self.email_entry.get().strip()
        server = self.server_entry.get().strip()
        self.status_label.config(text="")

        if not self.username:
            messagebox.showwarning("Error", "Enter username")
            return
        if not self.password:
            messagebox.showwarning("Error", "Enter password")
            return
        if self.auth_mode.get() == "register" and not self.email:
            messagebox.showwarning("Error", "Enter email")
            return

        if not re.match(r'^[A-Za-z0-9_]+$', self.username):
            messagebox.showwarning("Error", "Only latin, numbers and _")
            return

        try:
            host, port = server.split(":")
            self.host = host
            self.port = int(port)
        except:
            messagebox.showerror("Error", "Wrong server format")
            return

        try:

            self.client_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.client_socket.connect((self.host, self.port))

            # SEND LOGIN (IMPORTANT)

            login_data = json.dumps({
                "action": self.auth_mode.get(),
                "username": self.username,
                "password": self.password,
                "email": self.email
            }) + "\n"

            self.client_socket.send(login_data.encode())

            # Wait auth response
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
                self.status_label.config(text="Code sent to email")
                self.code_frame.pack(pady=10)
                self.connect_btn.config(state="disabled")
                return

            if resp.get("status") != "ok":
                msg = resp.get("message", "Auth failed")
                self.client_socket.close()
                messagebox.showerror("Error", msg)
                return

            self.prebuffer = rest
            self.running = True

            self.show_chat_screen()

            threading.Thread(
                target=self.receive_messages,
                daemon=True
            ).start()

        except Exception as e:
            messagebox.showerror("Error", str(e))


    def verify_code(self):

        code = self.code_entry.get().strip()
        if not code:
            self.status_label.config(text="Enter code")
            return

        try:
            if not self.pending_socket:
                self.status_label.config(text="No pending verification")
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
                except:
                    pass
                self.pending_socket = None
                self.show_login_screen("Welcome!")
                return

            msg = resp.get("message", "Verification failed")
            self.status_label.config(text=msg)

        except Exception as e:
            self.status_label.config(text=str(e))


    # ================= CHAT =================

    def show_chat_screen(self):

        for w in self.main_frame.winfo_children():
            w.destroy()

        self.chat_frame = tk.Frame(self.main_frame, bg=self.colors['bg'])
        self.chat_frame.pack(expand=True, fill="both", padx=15, pady=15)

        # Top

        top = tk.Frame(self.chat_frame, bg=self.colors['secondary'])
        top.pack(fill="x")

        tk.Label(
            top,
            text=f"{self.username} @ {self.host}:{self.port}",
            bg=self.colors['secondary'],
            fg="white"
        ).pack(side="left", padx=10)

        tk.Button(
            top,
            text="Disconnect",
            command=self.disconnect,
            bg=self.colors['warning'],
            fg="white"
        ).pack(side="right", padx=10)

        # Messages

        self.message_area = scrolledtext.ScrolledText(
            self.chat_frame,
            state="disabled",
            bg=self.colors['bg'],
            fg="white",
            font=self.message_font,
            wrap="word"
        )

        self.message_area.pack(expand=True, fill="both", pady=10)

        # Tags

        self.message_area.tag_config(
            "me",
            background=self.colors['my_message_bg']
        )

        self.message_area.tag_config(
            "other",
            background=self.colors['message_bg']
        )

        self.message_area.tag_config(
            "system",
            background=self.colors['system_bg'],
            justify="center"
        )

        # Bottom

        bottom = tk.Frame(self.chat_frame, bg=self.colors['bg'])
        bottom.pack(fill="x")

        self.message_entry = tk.Text(
            bottom,
            height=3,
            bg=self.colors['secondary'],
            fg="white",
            insertbackground="white"
        )

        self.message_entry.pack(side="left", expand=True, fill="x", padx=5)

        self.message_entry.bind("<Return>", self.send_enter)

        tk.Button(
            bottom,
            text="Send",
            command=self.send_message,
            bg=self.colors['accent'],
            fg="white",
            padx=20
        ).pack(side="right")


    # ================= SEND =================

    def send_enter(self, event):

        if event.state == 0:
            self.send_message()
            return "break"


    def send_message(self):

        msg = self.message_entry.get("1.0", tk.END).strip()

        if not msg:
            return

        try:

            data = json.dumps({
                "message": msg
            }) + "\n"

            self.client_socket.send(data.encode())

            self.message_entry.delete("1.0", tk.END)

        except:
            messagebox.showerror("Error", "Send failed")


    # ================= RECEIVE =================

    def receive_messages(self):

        buffer = self.prebuffer
        self.prebuffer = ""

        while self.running:

            try:

                data = self.client_socket.recv(1024).decode()

                if not data:
                    break

                buffer += data

                while "\n" in buffer:

                    line, buffer = buffer.split("\n", 1)

                    if not line.strip():
                        continue

                    msg = json.loads(line)

                    self.root.after(0, self.display_message, msg)

            except:
                break

        self.root.after(0, self.connection_lost)


    # ================= DISPLAY =================

    def display_message(self, data):

        self.message_area.config(state="normal")

        user = data.get("username", "")
        text = data.get("message", "")
        time = data.get("timestamp", "")
        tp = data.get("type", "message")

        if tp == "system":

            self.message_area.insert(
                tk.END,
                f"\n{text}\n",
                "system"
            )

        else:

            tag = "me" if user == self.username else "other"

            name = "You" if user == self.username else user

            self.message_area.insert(
                tk.END,
                f"\n{name} [{time}]\n{text}\n",
                tag
            )

        self.message_area.see(tk.END)
        self.message_area.config(state="disabled")


    # ================= DISCONNECT =================

    def disconnect(self):

        self.running = False

        try:
            self.client_socket.close()
        except:
            pass

        self.show_login_screen()


    def connection_lost(self):

        if self.running:

            self.running = False

            messagebox.showinfo("Info", "Connection lost")

            self.show_login_screen()


    def on_closing(self):

        self.running = False

        try:
            self.client_socket.close()
        except:
            pass

        self.root.destroy()


# ================= MAIN =================


def main():

    root = tk.Tk()

    app = MAXutkoClient(root)

    root.protocol("WM_DELETE_WINDOW", app.on_closing)

    root.mainloop()


if __name__ == "__main__":
    main()
