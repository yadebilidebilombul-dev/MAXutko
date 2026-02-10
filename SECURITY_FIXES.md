# 🔧 ПРИМЕРЫ ИСПРАВЛЕНИЯ УЯЗВИМОСТЕЙ

## SEC-001: Исправление хеширования паролей

### ДО (УЯЗВИМО):
```python
import hashlib

def _hash_password(self, password, salt):
    return hashlib.sha256((salt + password).encode()).hexdigest()

def verify_password(self, stored, password):
    salt = stored.get("salt", "")
    h = stored.get("hash", "")
    return self._hash_password(password, salt) == h
```

### ПОСЛЕ (БЕЗОПАСНО):
```python
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError

class MAXutkoServer:
    def __init__(self, ...):
        self.ph = PasswordHasher(
            time_cost=2,        # 2 iterations
            memory_cost=65536,  # 64 MB
            parallelism=4,      # 4 threads
            hash_len=16,        # 16 bytes output
            salt_len=16         # strong salt
        )
    
    def _hash_password(self, password, salt=None):
        """Argon2id хеширование"""
        # Примечание: PasswordHasher сам генерирует соль
        return self.ph.hash(password)
    
    def verify_password(self, stored, password):
        """Проверка пароля против хеша Argon2"""
        try:
            self.ph.verify(stored.get("hash", ""), password)
            return True
        except VerifyMismatchError:
            return False
```

**Установка**:
```bash
pip install argon2-cffi
```

---

## SEC-002: Шифрование токенов на диске

### ДО (УЯЗВИМО):
```python
def load_tokens(self):
    with open(self.tokens_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return data  # Открытый текст!

def save_tokens(self):
    with open(self.tokens_path, "w", encoding="utf-8") as f:
        json.dump(self.tokens, f, ensure_ascii=False, indent=2)
```

### ПОСЛЕ (БЕЗОПАСНО):
```python
from cryptography.fernet import Fernet
import base64
import os

class MAXutkoServer:
    def __init__(self, ...):
        self._init_encryption_key()
    
    def _init_encryption_key(self):
        """Инициализация мастер-ключа шифрования"""
        key_file = os.path.join(os.path.dirname(__file__), ".encryption_key")
        
        if not os.path.exists(key_file):
            # Генерируем новый ключ и сохраняем СЕКРЕТНО
            key = Fernet.generate_key()  # 32 байта base64
            with open(key_file, "wb") as f:
                f.write(key)
            os.chmod(key_file, 0o600)  # Только владелец может читать
            self.cipher = Fernet(key)
        else:
            with open(key_file, "rb") as f:
                key = f.read()
            self.cipher = Fernet(key)
    
    def load_tokens(self):
        """Загрузить и расшифровать токены"""
        if not os.path.exists(self.tokens_path):
            return {}
        
        try:
            with open(self.tokens_path, "rb") as f:
                encrypted_data = f.read()
            
            # Расшифровка
            plaintext = self.cipher.decrypt(encrypted_data)
            data = json.loads(plaintext.decode("utf-8"))
            
            # Очистить истёкшие токены
            now = time.time()
            cleaned = {}
            for token, item in data.items():
                if item.get("expires_at", 0) > now and item.get("username"):
                    cleaned[token] = item
            
            return cleaned
        except Exception as e:
            print(f"[!] Failed to decrypt tokens: {e}")
            return {}
    
    def save_tokens(self):
        """Сохранить и зашифровать токены"""
        try:
            plaintext = json.dumps(self.tokens, ensure_ascii=False, indent=2)
            encrypted_data = self.cipher.encrypt(plaintext.encode("utf-8"))
            
            with open(self.tokens_path, "wb") as f:
                f.write(encrypted_data)
        except Exception as e:
            print(f"[!] Failed to encrypt tokens: {e}")
```

**Важно**: Ключ шифрования должен быть:
- Сохранён на диске с вычисляемо-защищённых доступом (0o600)
- Или загружен из переменной окружения: `MAXUTKO_ENCRYPTION_KEY`

---

## SEC-003: Включение TLS 1.3

### ДО (УЯЗВИМО):
```python
def __init__(self, host=None, port=5555):
    self.server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    self.server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    self.server.bind((self.host, self.port))
    self.server.listen(100)
    # БЕЗ ШИФРОВАНИЯ!
```

### ПОСЛЕ (БЕЗОПАСНО):

**Сервер**:
```python
import ssl

class MAXutkoServer:
    def __init__(self, host=None, port=5555, certfile="cert.pem", keyfile="key.pem"):
        self.host = host
        self.port = port
        
        # Создаём SSL контекст
        self.ssl_context = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
        self.ssl_context.load_cert_chain(certfile, keyfile)
        
        # Требуем TLS 1.3
        self.ssl_context.minimum_version = ssl.TLSVersion.TLSv1_3
        self.ssl_context.maximum_version = ssl.TLSVersion.TLSv1_3
        
        # Отключаем слабые шифры
        self.ssl_context.set_ciphers('ECDHE+AESGCM:DHE+AESGCM')
        
        # Создаём обычный сокет
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        
        # Оборачиваем в SSL
        self.server = self.ssl_context.wrap_socket(
            sock,
            server_side=True
        )
        
        self.server.bind((self.host, self.port))
        self.server.listen(100)
        
        print(f"[+] Server started on {self.host}:{self.port} with TLS 1.3")
    
    def start(self):
        while True:
            try:
                client, addr = self.server.accept()
                # client уже зашифрован!
                print(f"[+] TLS Connection from {addr}")
                
                threading.Thread(
                    target=self.handle_client,
                    args=(client, addr),
                    daemon=True
                ).start()
            except ssl.SSLError as e:
                print(f"[!] SSL Error: {e}")
```

**Сертификаты** (генерируем самоподписанные для тестирования):
```bash
# ВАЖНО: Для продакшена нужны сертификаты от доверённого CA

# Генерируем приватный ключ
openssl genrsa -out key.pem 4096

# Генерируем сертификат (действителен 365 дней)
openssl req -new -x509 -key key.pem -out cert.pem -days 365 \
  -subj "/CN=26.202.145.247"
```

**Клиент**:
```python
import ssl
import socket

class AuthWorker(QtCore.QThread):
    def run(self):
        try:
            # Создаём конекст
            context = ssl.create_default_context()
            
            # Для самоподписанного сертификата на тестирование:
            context.check_hostname = False
            context.verify_mode = ssl.CERT_NONE
            
            # Для продакшена: загружаем CA сертификат
            # context.load_verify_locations("ca-cert.pem")
            # context.check_hostname = True
            # context.verify_mode = ssl.CERT_REQUIRED
            
            # Требуем минимум TLS 1.3
            context.minimum_version = ssl.TLSVersion.TLSv1_3
            
            # Создаём сокет и подключаемся
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock = context.wrap_socket(sock, server_hostname=self.host)
            sock.connect((self.host, self.port))
            
            # Отправляем данные уже зашифрованные
            login_data = json.dumps({
                "action": self.mode,
                "username": self.username,
                "password": self.password,  # Теперь зашифровано TLS!
                ...
            }) + "\n"
            sock.send(login_data.encode())
            
            # Получаем ответ (зашифрованный)
            ...
```

---

## SEC-005: Исправление бесконечной рекурсии

### ДО (УЯЗВИМО):
```python
def display_message(self, data):
    if data.get("type") == "history":
        items = data.get("items", [])
        for item in items:
            if isinstance(item, dict):
                self.display_message(item)  # БЕЗ ЛИМИТА!
        return
```

### ПОСЛЕ (БЕЗОПАСНО):
```python
MAX_MESSAGE_DEPTH = 5  # Глобальная константа

def display_message(self, data, depth=0):
    """Безопасная обработка сообщений с лимитом глубины"""
    
    if depth > MAX_MESSAGE_DEPTH:
        print(f"[!] Message nesting too deep (>{MAX_MESSAGE_DEPTH}), skipping")
        return
    
    data = data or {}
    
    if not isinstance(data, dict):
        print(f"[!] Invalid message format at depth {depth}")
        return
    
    msg_type = data.get("type", "").lower()
    
    if msg_type == "history":
        items = data.get("items", [])
        
        if not isinstance(items, list):
            print(f"[!] Invalid history items at depth {depth}")
            return
        
        if len(items) > 5000:  # Лимит на количество элементов
            print(f"[!] Too many history items: {len(items)} > 5000")
            items = items[:5000]
        
        for idx, item in enumerate(items):
            if isinstance(item, dict):
                self.display_message(item, depth + 1)  # ← Увеличиваем глубину
        return
    
    elif msg_type == "dm":
        self._display_direct_message(data)
    
    elif msg_type == "message":
        self._display_broadcast_message(data)
    
    elif msg_type == "file":
        self._display_file_message(data)
    
    else:
        print(f"[!] Unknown message type at depth {depth}: {msg_type}")
```

---

## SEC-006: Безопасный passcode

### ДО (УЯЗВИМО):
```python
def _build_passcode(self, username, password, timestamp):
    pw_hash = self._hash_plain(password)
    user_hash = self._hash_plain(username)
    ts_hash = self._hash_plain(timestamp)
    combined = (int(pw_hash, 16) + int(user_hash, 16)) * int(ts_hash, 16)
    return format(combined, "x")  # Может быть переборен!
```

### ПОСЛЕ (БЕЗОПАСНО):
```python
def _generate_passcode(self):
    """Генерировать криптостойкий passcode"""
    # 32 байта = 256 бит энтропии
    passcode_bytes = secrets.token_bytes(32)
    passcode_hex = passcode_bytes.hex()  # 64 символа
    return passcode_hex

def _hash_passcode(self, passcode):
    """Хешировать passcode перед сохранением"""
    # Используем SHA-256 для хеша (не для пароля!)
    return hashlib.sha256(passcode.encode()).hexdigest()

def _handle_passcode_create_request(self, client, username, password):
    """Создание passcode"""
    if not password:
        self.send_passcode(client, "error", "Введите пароль", mode="create")
        return
    
    with self.accounts_lock:
        acc = self.accounts.get(username)
    
    if not acc:
        self.send_passcode(client, "error", "Пользователь не найден", mode="create")
        return
    
    if not self.verify_password(acc, password):
        self.send_passcode(client, "error", "Неверный пароль", mode="create")
        return
    
    existing = (acc.get("passcode_hash") or "").strip()
    if existing:
        self.send_passcode(client, "error", "PassCodeHash уже настроен", mode="create")
        return
    
    stored_email = (acc.get("email") or "").strip()
    if not stored_email:
        self.send_passcode(client, "error", "Почта не указана", mode="create")
        return
    
    # ← НОВОЕ: генерируем криптостойкий passcode
    passcode = self._generate_passcode()
    passcode_hash = self._hash_passcode(passcode)
    
    # Генерируем код подтверждения
    code = CodeGenerator.generate_code(6)
    expires_at = time.time() + 300
    
    with self.passcode_pending_lock:
        self.passcode_pending[username] = {
            "code": code,
            "expires_at": expires_at,
            "passcode": passcode,  # Запомнить для отправки после верификации
            "passcode_hash": passcode_hash,  # Сохранить хеш
            "mode": "create"
        }
    
    self.mailer.send_code(stored_email, code)
    masked = self._mask_email(stored_email)
    self.send_passcode(
        client,
        "need_code",
        f"Код отправлен на почту профиля: {masked}",
        mode="create"
    )

def _handle_passcode_verify(self, client, username, code, mode):
    """Проверка кода и сохранение passcode"""
    with self.passcode_pending_lock:
        pending = self.passcode_pending.get(username)
    
    if not pending:
        self.send_passcode(client, "error", "Нет ожидания, запросите заново", mode=mode)
        return
    
    if time.time() > pending.get("expires_at", 0):
        with self.passcode_pending_lock:
            self.passcode_pending.pop(username, None)
        self.send_passcode(client, "error", "Код истёк", mode=mode)
        return
    
    if code != pending.get("code"):
        self.send_passcode(client, "error", "Неверный код", mode=mode)
        return
    
    # ← Код верифицирован, сохраняем passcode хеш
    passcode_hash = pending.get("passcode_hash")
    passcode = pending.get("passcode")  # Только для отправки клиенту!
    
    with self.accounts_lock:
        acc = self.accounts.get(username)
        if acc:
            acc["passcode_hash"] = passcode_hash
            self.accounts[username] = acc
            self.save_accounts()
    
    with self.passcode_pending_lock:
        self.passcode_pending.pop(username, None)
    
    # Отправляем только самому пользователю через TLS!
    self.send_passcode(
        client,
        "ok",
        "PassCodeHash создан",
        mode=mode,
        passcode=passcode  # ← Отправляем!
    )
```

---

## SEC-007: Подпись на обновления

### Генерация подписи (на сервере разработки):
```python
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa, padding
from cryptography.hazmat.backends import default_backend
import base64

# Генерируем RSA-4096 пару (один раз!)
private_key = rsa.generate_private_key(
    public_exponent=65537,
    key_size=4096,
    backend=default_backend()
)

# Сохраняем приватный ключ (СЕКРЕТНО!)
private_pem = private_key.private_bytes(
    encoding=serialization.Encoding.PEM,
    format=serialization.PrivateFormat.TraditionalOpenSSL,
    encryption_algorithm=serialization.BestAvailableEncryption(b"PASSWORD")
)
with open("signing_key.pem", "wb") as f:
    f.write(private_pem)

# Экспортируем публичный ключ (публично!)
public_key = private_key.public_key()
public_pem = public_key.public_bytes(
    encoding=serialization.Encoding.PEM,
    format=serialization.PublicFormat.SubjectPublicKeyInfo
)
with open("public_key.pem", "wb") as f:
    f.write(public_pem)

# Подписываем MAXutko.exe
with open("MAXutko.exe", "rb") as f:
    exe_data = f.read()

signature = private_key.sign(
    exe_data,
    padding.PSS(
        mgf=padding.MGF1(hashes.SHA256()),
        salt_length=padding.PSS.MAX_LENGTH
    ),
    hashes.SHA256()
)

signature_b64 = base64.b64encode(signature).decode("ascii")
print(f"Signature: {signature_b64[:100]}...")

# Это сохранить в Updates.json:
updates = {
    "version": "2.2.0",
    "changelog": "...",
    "exe_hash": hashlib.sha512(exe_data).hexdigest(),
    "exe_signature": signature_b64
}
```

### Проверка подписи (на клиенте):
```python
import hashlib
import base64
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.backends import default_backend

# Встроенный публичный ключ в клиент (из public_key.pem)
PUBLIC_KEY_PEM = b"""-----BEGIN PUBLIC KEY-----
MIICIjANBgkqhkiG9w0BAQEFAAOCAg8AMIICCgKCAgEAx7Z...
-----END PUBLIC KEY-----"""

def verify_update(exe_path, update_info):
    """Проверить подпись и хеш обновления"""
    
    try:
        # Загружаем публичный ключ
        public_key = serialization.load_pem_public_key(
            PUBLIC_KEY_PEM,
            backend=default_backend()
        )
        
        # Читаем загруженный файл
        with open(exe_path, "rb") as f:
            exe_data = f.read()
        
        # Проверяем SHA-512 хеш
        computed_hash = hashlib.sha512(exe_data).hexdigest()
        expected_hash = update_info.get("exe_hash", "")
        
        if computed_hash != expected_hash:
            return False, "Хеш файла не совпадает"
        
        # Проверяем подпись
        signature_b64 = update_info.get("exe_signature", "")
        signature = base64.b64decode(signature_b64)
        
        public_key.verify(
            signature,
            exe_data,
            padding.PSS(
                mgf=padding.MGF1(hashes.SHA256()),
                salt_length=padding.PSS.MAX_LENGTH
            ),
            hashes.SHA256()
        )
        
        return True, "Подпись верна"
    
    except Exception as e:
        return False, f"Ошибка проверки: {str(e)}"

# Использование:
class UpdateDownloadWorker(QtCore.QThread):
    def run(self):
        # ... скачиваем MAXutko.exe ...
        
        # Проверяем подпись
        is_valid, message = verify_update("MAXutko.exe", update_info)
        
        if not is_valid:
            self.error.emit(f"Обновление поддельное: {message}")
            # Удаляем файл!
            os.remove("MAXutko.exe")
            return
        
        # Подпись верна, можно установить
        self.finished.emit("MAXutko.exe", version)
```

---

## SEC-008: Улучшенная защита от брутфорса

```python
class MAXutkoServer:
    def __init__(self):
        # ... существующий код ...
        self.bruteforce_attempts = {}  # username -> {ip -> [timestamps]}
        self.bruteforce_block = {}     # username -> unblock_time
    
    def _check_bruteforce(self, ip, username):
        """Улучшенная защита от брутфорса"""
        now = time.time()
        
        # Проверяем блокировку по username
        if username in self.bruteforce_block:
            if now < self.bruteforce_block[username]:
                return True  # Заблокирован
            else:
                del self.bruteforce_block[username]
        
        # Отслеживаем попытки по (username, ip)
        key = f"{username}:{ip}"
        
        with self.rate_limits_lock:
            bucket = self.rate_limits.get(key, [])
            # Очищаем старые записи (за последние 5 минут)
            bucket = [t for t in bucket if now - t < 300]
            
            # Проверяем количество попыток
            if len(bucket) >= 5:  # 5 неудачных попыток
                # Exponential backoff
                block_duration = min(
                    1800,  # MAX 30 минут
                    60 * (2 ** len([1 for _ in bucket if _ > now - 60]))  # За последний час
                )
                
                self.bruteforce_block[username] = now + block_duration
                
                print(f"[!] Account {username} from {ip} blocked for {block_duration}s")
                return True
            
            # Добавляем попытку в счётчик
            bucket.append(now)
            self.rate_limits[key] = bucket
        
        return False
    
    def on_login_failure(self, ip, username):
        """Вызывается при неудачном входе"""
        self._check_bruteforce(ip, username)
```

---

## СТАРТОВАЯ КОМАНДА ДЛЯ ИСПРАВЛЕНИЙ

```bash
# 1. Установить зависимости
pip install argon2-cffi cryptography

# 2. Включить логирование
mkdir -p /var/log/maxutko
chmod 700 /var/log/maxutko

# 3. Сгенерировать TLS сертификаты
openssl genrsa -out server/key.pem 4096
openssl req -new -x509 -key server/key.pem -out server/cert.pem -days 365

# 4. Сгенерировать ключ шифрования
python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())" > server/.encryption_key
chmod 600 server/.encryption_key

# 5. Отредактировать код согласно примерам выше
```

---

## ТОП-5 КРИТИЧЕСКИХ ИЗМЕНЕНИЙ

1. **Включить TLS** - без этого всё остальное бесполезно
2. **Поменять хеширование паролей** на Argon2
3. **Защитить токены** - шифровать на диске
4. **Исправить рекурсию** - добавить лимит глубины
5. **Логировать атаки** - обнаружить проблемы в реальном времени
