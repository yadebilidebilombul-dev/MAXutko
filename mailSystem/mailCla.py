import os
import smtplib
import ssl
import secrets
from email.message import EmailMessage
from code import SPECIAL_CODE

SwitchMail = True
CONSOLE_EMAIL = SPECIAL_CODE

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
    return _coerce_bool(os.getenv("SWITCH_MAIL"), SwitchMail)

def is_console_email(email):
    return (email or "").strip().lower() == CONSOLE_EMAIL

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
        self.username = username or os.getenv("SMTP_USER", "moro23728@gmail.com")
        self.password = password or os.getenv("SMTP_PASS", "gvgl vwwy fycp iwni")
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
            print(f"[MAIL] Console override. Code for {to_email}: {code}")
            return True

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

        try:
            if self.smtp_port == 465:
                # Use SMTP_SSL for port 465
                context = ssl.create_default_context()
                with smtplib.SMTP_SSL(self.smtp_host, self.smtp_port, context=context, timeout=15) as server:
                    server.login(self.username, self.password)
                    server.send_message(msg)
            else:
                # Use SMTP with STARTTLS for port 587 (Gmail, Yandex, etc)
                context = ssl.create_default_context()
                with smtplib.SMTP(self.smtp_host, self.smtp_port, timeout=15) as server:
                    server.starttls(context=context)
                    server.login(self.username, self.password)
                    server.send_message(msg)
            print(f"[MAIL] Code sent to {to_email}: {code}")
            return True
        except ssl.SSLError as e:
            print(f"[MAIL] SSL Error for {to_email}: {e}")
            print(f"[MAIL] FALLBACK (SSL): Code for {to_email}: {code}")
            return True
        except smtplib.SMTPAuthenticationError as e:
            print(f"[MAIL] Authentication error for {to_email}: {e}")
            print(f"[MAIL] FALLBACK (AUTH): Code for {to_email}: {code}")
            return True
        except smtplib.SMTPServerDisconnected as e:
            print(f"[MAIL] Server disconnected for {to_email}: {e}")
            print(f"[MAIL] FALLBACK (DISCONN): Code for {to_email}: {code}")
            return True
        except smtplib.SMTPException as e:
            print(f"[MAIL] SMTP error for {to_email}: {e}")
            print(f"[MAIL] FALLBACK (SMTP): Code for {to_email}: {code}")
            return True
        except Exception as e:
            print(f"[MAIL] Unexpected error for {to_email}: {e}")
            print(f"[MAIL] FALLBACK (OTHER): Code for {to_email}: {code}")
            return True
