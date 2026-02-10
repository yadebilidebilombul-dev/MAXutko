import smtplib
import ssl

smtp_host = "smtp.yandex.ru"
smtp_port = 587
username = "iwantraiker@yandex.ru"
password = "faozfkssqnoryltl"

try:
    print(f"[*] Connecting to {smtp_host}:{smtp_port}...")
    context = ssl.create_default_context()
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    
    server = smtplib.SMTP(smtp_host, smtp_port, timeout=10)
    server.set_debuglevel(1)
    print(f"[+] Connected")
    
    print(f"[*] Starting TLS...")
    server.starttls(context=context)
    print(f"[+] TLS started")
    
    print(f"[*] Logging in as {username}...")
    server.login(username, password)
    print(f"[+] Logged in successfully")
    
    print(f"[*] Testing QUIT...")
    server.quit()
    print(f"[+] Connection closed")
    
except Exception as e:
    print(f"[!] Error: {e}")
    import traceback
    traceback.print_exc()
