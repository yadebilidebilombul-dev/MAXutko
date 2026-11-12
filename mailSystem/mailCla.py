import os
import smtplib
import ssl
import secrets
from email.message import EmailMessage

DEFAULT_SMTP_HOST = os.getenv("SMTP_HOST", "smtp.gmail.com")
DEFAULT_SMTP_PORT = os.getenv("SMTP_PORT", "465")


def _coerce_bool(value, default):
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        v = value.strip().lower()
        if v in ("1", "true", "yes", "y", "on"):
            return True
        if v in ("0", "false", "no", "n", "off"):
            return False
    return default


def _default_switch_mail():
    return _coerce_bool(os.getenv("SWITCH_MAIL"), True)


def is_console_email(email):
    # Консольный режим разрешён только при явно включённом флаге окружения ALLOW_CONSOLE_EMAIL=1
    if not _coerce_bool(os.getenv("ALLOW_CONSOLE_EMAIL"), False):
        return False
    console_email = os.getenv("CONSOLE_EMAIL", "").strip().lower()
    return bool(console_email) and (email or "").strip().lower() == console_email


class CodeGenerator:
    @staticmethod
    def generate_code(length=6):
        digits = "0123456789"
        return "".join(secrets.choice(digits) for _ in range(length))


class MailSender:
    def __init__(
        self,
        smtp_host=None,
        smtp_port=None,
        username=None,
        password=None,
        from_email=None,
        use_tls=True,
        use_ssl=None,
        SwitchMail=None
    ):
        self.smtp_host = smtp_host or os.getenv("SMTP_HOST", DEFAULT_SMTP_HOST)
        self.smtp_port = int(smtp_port or os.getenv("SMTP_PORT", DEFAULT_SMTP_PORT))
        # Никаких паролей в коде! Задавайте SMTP_USER и SMTP_PASS через переменные окружения (.env)
        self.username = username or os.getenv("SMTP_USER", "")
        self.password = password or os.getenv("SMTP_PASS", "")
        self.from_email = from_email or os.getenv("SMTP_FROM", self.username)
        self.use_tls = use_tls
        self.use_ssl = use_ssl
        if SwitchMail is None:
            SwitchMail = _default_switch_mail()
        self.SwitchMail = bool(SwitchMail)

    def is_configured(self):
        return bool(self.smtp_host and self.username and self.password and self.from_email)

    def send_code(self, to_email, code):
        if is_console_email(to_email):
            print(f"[MAIL] Console override active for {to_email}. Code: {code}")
            return True

        if not self.SwitchMail:
            print(f"[MAIL] SwitchMail disabled (dev mode). Code for {to_email}: {code}")
            return True

        if not self.is_configured():
            print("[MAIL] Error: SMTP credentials are not configured in environment variables.")
            return False

        msg = EmailMessage()
        msg["Subject"] = "Your confirmation code"
        msg["From"] = self.from_email
        msg["To"] = to_email
        msg.set_content(f"Your confirmation code: {code}")

        try:
            context = ssl.create_default_context()
            if self.smtp_port == 465:
                with smtplib.SMTP_SSL(self.smtp_host, self.smtp_port, context=context, timeout=15) as server:
                    server.login(self.username, self.password)
                    server.send_message(msg)
            else:
                with smtplib.SMTP(self.smtp_host, self.smtp_port, timeout=15) as server:
                    server.starttls(context=context)
                    server.login(self.username, self.password)
                    server.send_message(msg)
            print(f"[MAIL] Confirmation code successfully sent to {to_email}")
            return True
        except Exception as e:
            print(f"[MAIL] Failed to send email to {to_email}: {e}")
            return False