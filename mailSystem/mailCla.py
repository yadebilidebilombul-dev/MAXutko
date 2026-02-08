import os
import smtplib
import ssl
import secrets
from email.message import EmailMessage

SwitchMail = False


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
    return _coerce_bool(os.getenv("SWITCH_MAIL"), SwitchMail)

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
        self.smtp_host = smtp_host or os.getenv("SMTP_HOST", "smtp.yandex.ru")
        self.smtp_port = int(smtp_port or os.getenv("SMTP_PORT", "465"))
        self.username = username or os.getenv("SMTP_USER", "iwantraiker@yandex.ru")
        self.password = password or os.getenv("SMTP_PASS", "faozfkssqnoryltl")
        self.from_email = from_email or os.getenv("SMTP_FROM", self.username)
        self.use_tls = use_tls
        self.use_ssl = use_ssl
        if SwitchMail is None:
            SwitchMail = _default_switch_mail()
        self.SwitchMail = bool(SwitchMail)


    def is_configured(self):
        return bool(self.smtp_host and self.username and self.password and self.from_email)


    def send_code(self, to_email, code):

        if not self.SwitchMail:
            print(f"[MAIL] SwitchMail disabled. Code for {to_email}: {code}")
            return True

        if not self.is_configured():
            print(f"[MAIL] SMTP not configured. Code for {to_email}: {code}")
            return True

        msg = EmailMessage()
        msg["Subject"] = "Your confirmation code"
        msg["From"] = self.from_email
        msg["To"] = to_email
        msg.set_content(f"Your confirmation code: {code}")

        context = ssl.create_default_context()

        use_ssl = self.use_ssl
        if use_ssl is None:
            use_ssl = self.smtp_port == 465

        if use_ssl:
            with smtplib.SMTP_SSL(self.smtp_host, self.smtp_port, context=context) as server:
                server.login(self.username, self.password)
                server.send_message(msg)
        else:
            with smtplib.SMTP(self.smtp_host, self.smtp_port) as server:
                if self.use_tls:
                    server.starttls(context=context)
                server.login(self.username, self.password)
                server.send_message(msg)

        return True
