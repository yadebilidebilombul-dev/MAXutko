import os
import sys

BASE_DIR = os.path.abspath(os.path.dirname(__file__))
sys.path.append(BASE_DIR)

from mailSystem.mailCla import MailSender, CodeGenerator


def main():
    to_email = os.getenv("TEST_TO")
    if not to_email:
        to_email = input("Recipient email (TEST_TO): ").strip()
        if not to_email:
            print("Recipient email is required.")
            return 1

    mailer = MailSender()
    code = CodeGenerator.generate_code(6)

    try:
        mailer.send_code(to_email, code)
        print("Sent:", to_email, "code:", code)
        return 0
    except Exception as e:
        print("Send failed:", e)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
