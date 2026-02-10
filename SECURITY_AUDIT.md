# 🔒 ПОЛНЫЙ АУДИТ БЕЗОПАСНОСТИ MAXUTKO

**Дата аудита**: 10 февраля 2026  
**Статус**: 30 выявленных уязвимостей  
**Рекомендация**: Критическое переделывание архитектуры безопасности  

---

## 📋 ТАБЛИЦА ВСЕХ УЯЗВИМОСТЕЙ

| # | ID | Название уязвимости | Описание | Критичность | CVSS | Компонент | Строки кода | CWE | Решение |
|---|---|---|---|---|---|---|---|---|---|
| 1 | SEC-001 | Слабое хеширование паролей (SHA256) | Пароли хешируются как `SHA256(salt + password)` вместо bcrypt/argon2. Уязвимо для GPU-атак и rainbow-таблиц | 🔴 CRITICAL | 9.8 | Сервер | 451-453 | CWE-327 | Использовать `argon2id` с параметрами: time=2, memory=65536, parallelism=4 |
| 2 | SEC-002 | Открытое хранение токенов доступа | `tokens.json` содержит активные токены без шифрования. TTL 30 дней | 🔴 CRITICAL | 9.9 | Сервер | 262, 270 | CWE-312, CWE-798 | Использовать `cryptography.Fernet` для шифрования на диске, сократить TTL до 1 дня |
| 3 | SEC-003 | Незащищённая передача по сети (plaintext TCP) | Весь трафик (пароли, токены, сообщения) передаётся по открытому TCP без шифрования | 🔴 CRITICAL | 10.0 | Оба (Сервер+Клиент) | 33-39, 79-83 | CWE-295, OWASP-A02-2021 | Обязательно TLS 1.3, сертификаты, требовать pinning |
| 4 | SEC-004 | Утечка пароля в локальный файл | Токены сохраняются в `auth_tokens.json` в открытом виде с паролям и данными доступа | 🔴 CRITICAL | 9.8 | Клиент | 2289-2303, 2294 | CWE-312 | Использовать Windows Credential Manager или keyring; шифровать с маслом |
| 5 | SEC-005 | Бесконечная рекурсия в парсинге истории | Функция `display_message()` рекурсивно вызывает себя без лимита глубины при обработке `history` | 🔴 CRITICAL | 9.1 | Клиент | 3500-3512 | CWE-674 | Добавить счётчик глубины, максимум 5 уровней; использовать iterative processing |
| 6 | SEC-006 | Непредсказуемый passcode из известных значений | Passcode = `(hash(pwd) + hash(user)) * hash(timestamp)` - timestamp известен, может быть переборен | 🔴 CRITICAL | 8.8 | Сервер | 434-441 | CWE-330 | Использовать `secrets.token_bytes(32)` и сохранять хеш на диске |
| 7 | SEC-007 | Отсутствие подписи на обновлениях | Обновления скачиваются без проверки хеша/подписи. MITM может установить вредонос | 🔴 CRITICAL | 9.6 | Клиент | 383-422 | CWE-347 | Использовать RSA-4096 подпись; SHA-512 хеш; пубключ в бинарнике |
| 8 | SEC-008 | Слабая защита от брутфорса | Блокировка IP на 60 сек при 10 попытках - обходится распределённой атакой, неправильно подсчитывается | 🟠 HIGH | 7.5 | Сервер | 107-114, 576-593 | CWE-307 | Rate limiting по `(username, IP)`, exponential backoff, CAPTCHA, 2FA |
| 9 | SEC-009 | Утечка информации о статусе пользователя | Функция `_search_users()` возвращает поле `"active"`, раскрывая когда пользователи в сети | 🟠 HIGH | 6.5 | Сервер | 1206-1227 | CWE-200 | Удалить поле `active` из результатов поиска; не раскрывать статус |
| 10 | SEC-010 | Отсутствие лимита размера сообщений | Сообщения записываются в историю без проверки на max size. DoS-атака через гигантские сообщения | 🟠 HIGH | 7.3 | Сервер | 1621-1631 | CWE-406 | Добавить проверку `len(text) <= 10000`, размер истории <= 100 MB |
| 11 | SEC-011 | Race condition при разрыве соединения | `remove_client()` не синхронизирован с обращениями в `handle_client()`. Возможен доступ к закрытому сокету | 🟠 HIGH | 6.1 | Сервер | 1555-1571, 2423-2438 | CWE-362 | Использовать семафоры; проверять `socket.fileno() == -1` перед send |
| 12 | SEC-012 | Слабая энтропия ID сообщений | `secrets.token_hex(12)` = 96 бит. При 100M сообщений вероятность коллизии ~1% | 🟠 HIGH | 6.3 | Сервер | 1968, 2015 | CWE-330 | Использовать `secrets.token_hex(32)` - 256 бит энтропии |
| 13 | SEC-013 | Directory traversal при загрузке файлов | `_sanitize_filename()` удаляет символы, но `....//....//` после декодирования может пройти | 🟠 HIGH | 7.2 | Сервер | 443-447 | CWE-22 | Использовать `pathlib.Path`, явное разрешение только `[a-zA-Z0-9._-]` |
| 14 | SEC-014 | Отсутствие логирования попыток доступа | Нет логов брутфорса, неудачных входов, удаления аккаунтов | 🟠 HIGH | 7.0 | Сервер | all | CWE-778 | Логировать в `/var/log/maxutko/` с timestamp, IP, user, результат |
| 15 | SEC-015 | Файлы недоступны при удалении аккаунта | При удалении профиля файлы остаются в `/files/`. Утечка данных | 🟠 HIGH | 7.1 | Сервер | 2200-2400 | CWE-404 | На удаление профиля: удалить все файлы в `/files/*_owner.ext` |
| 16 | SEC-016 | Пароль хранится в памяти клиента | Переменная `self.password` хранится до конца сессии. Memory dump → утечка | 🟠 HIGH | 6.8 | Клиент | various | CWE-312 | Очищать пароль после аутентификации: `password = None; gc.collect()` |
| 17 | SEC-017 | Отсутствие E2E шифрования (по дизайну) | Приватные сообщения шифруются только TLS - сервер видит plaintext | 🟠 HIGH | 7.2 | Оба | all | CWE-311 | Реализовать Signal Protocol или Double Ratchet для каждой пары пользователей |
| 18 | SEC-018 | Нет верификации email при регистрации | Можно забить любой email, даже `aaa@aaa.aaa`. Затем забыть пароль | 🟡 MEDIUM | 5.3 | Сервер | 1339-1362 | CWE-20 | Проверить `is_console_email()`, отправить verif code, требовать подтверждения |
| 19 | SEC-019 | Отсутствие rate limiting на отправку сообщений | Один клиент может отправить миллион сообщений за секунду - DoS | 🟡 MEDIUM | 6.5 | Сервер | 1621-1631 | CWE-400 | Rate limit: `max 10 msg/sec per user`, очередь на сервере |
| 20 | SEC-020 | Нет защиты от DDoS на уровне приложения | Один клиент может отправить множество tiny пакетов, заполнив буфер | 🟡 MEDIUM | 6.2 | Сервер | socket.listen(100) | CWE-400 | Использовать asyncio, ограничить буфер, разрывать idle соединения |
| 21 | SEC-021 | Непредсказуемая проверка администраторов | Проверка `if self._get_account_clan(requester) != "Одмен"` - может быть bag в `_refresh_account_clan` | 🟡 MEDIUM | 5.4 | Сервер | 1889-1905 | CWE-639 | Явно проверять в DB, использовать role-based permission matrix |
| 22 | SEC-022 | Passcode виден при отправке в очередь | Passcode отправляется в очередь `self.sender_thread` перед шифрованием | 🟡 MEDIUM | 5.1 | Клиент | 2077-2081 | CWE-312 | Не отправлять passcode через очередь; отправлять сразу через TLS |
| 23 | SEC-023 | Утечка пути к скачанным файлам | `download_cache.json` содержит абсолютные пути типа `C:\Users\Name\Downloads\...` | 🟡 MEDIUM | 5.9 | Клиент | 2203-2218 | CWE-200 | Хранить только относительные пути; шифровать на диске |
| 24 | SEC-024 | Нет повторной аутентификации для критических операций | Создание passcode не требует пароля. Если сессия украдена → заблокировать хозяина | 🟡 MEDIUM | 5.7 | Сервер | 610-641, 656-675 | CWE-640 | Требовать password re-entry для passcode, email change, 2FA |
| 25 | SEC-025 | JSON Injection риск при некорректных данных | Если сервер отправит некорректный JSON - клиент может упасть или зависнуть | 🟡 MEDIUM | 5.0 | Клиент | 3500-3510 | CWE-91 | Валидировать JSON схему на обеих сторонах; использовать Pydantic |
| 26 | SEC-026 | Отсутствие валидации профиля при обновлении | Поле `nick`, `about`, `avatar` не проверяются на max length | 🟡 MEDIUM | 5.2 | Сервер | 1664-1690 | CWE-20 | Ограничить `nick <= 32 chars`, `about <= 500 chars`, `avatar <= 300KB` |
| 27 | SEC-027 | Открытое хранение настроек клиента | `settings.json` содержит все настройки в открытом виде | 🟡 MEDIUM | 4.8 | Клиент | 2305-2318 | CWE-312 | Шифровать `settings.json` с маслом; использовать keyring |
| 28 | SEC-028 | Нет timeout на операции с сокетом | `socket.recv(1024)` может зависнуть бесконечно, если сервер выключен | 🟡 MEDIUM | 4.3 | Клиент | 111-130 | CWE-391 | Добавить `sock.settimeout(30)` на все сокеты |
| 29 | SEC-029 | Утечка версии приложения | Версия `VersionBulid = "2.1.1"` отправляется при каждом входе | 🟡 MEDIUM | 4.3 | Клиент | 18 | CWE-200 | Не отправлять версию; делать обновления незаметными |
| 30 | SEC-030 | Отсутствие CSRF защиты для веб-интерфейса (если добавится) | Если позже будет веб-интерфейс - нет защиты от CSRF | 🟡 MEDIUM | 6.5 | Будущее | - | CWE-352 | Использовать CSRF tokens в Body (не в Header); SameSite Cookies |

---

## 🔴 КРИТИЧЕСКИЕ УЯЗВИМОСТИ (IMMEDIATE ACTION)

### 1️⃣ SEC-001: Слабое хеширование паролей

**Текущий код** (строки 451-453):
```python
def _hash_password(self, password, salt):
    return hashlib.sha256((salt + password).encode()).hexdigest()
```

**Проблема**: 
- SHA256 очень быстро вычисляется → GPU/ASIC атаки за часы
- Rainbow-таблицы для распространённых паролей
- 16-байтовая соль недостаточна

**Атака**: На RTX 4090 можно перебрать 300M хешей в секунду. Словарь из 1M слов + 10 вариаций = ~10 сек на аккаунт

**Решение**:
```python
import argon2
self.ph = argon2.PasswordHasher(
    time_cost=2,        # 2 iterations
    memory_cost=65536,  # 64 MB
    parallelism=4       # 4 потока
)
return self.ph.hash(password)  # Takes ~1 second per hash
```

---

### 2️⃣ SEC-002: Открытое хранение токенов

**Текущий код** (токены.json):
```json
{
  "5e6b7a55edd43c49c664a3bb6d7bdad0d3ee4e7768f1a956": {
    "username": "denik",
    "expires_at": 1773154674.4542716
  }
}
```

**Риск**: Если attacker скопирует файл → полный доступ на 30 дней

**Решение**:
```python
from cryptography.fernet import Fernet

# Генерируем мастер-ключ один раз (или из пароля)
MASTER_KEY = Fernet.generate_key()  # Сохранить в .env

cipher = Fernet(MASTER_KEY)
encrypted_tokens = {
    token: cipher.encrypt(json.dumps(data).encode())
    for token, data in self.tokens.items()
}
```

---

### 3️⃣ SEC-003: Незащищённая передача по TCP

**Решение**: Обязательный TLS 1.3

**Сервер**:
```python
import ssl

context = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
context.load_cert_chain("cert.pem", "key.pem")
context.minimum_version = ssl.TLSVersion.TLSv1_3
context.options |= ssl.OP_NO_TLSv1_2

self.server = socket.socket()
self.server = context.wrap_socket(self.server, server_side=True)
self.server.bind((self.host, self.port))
```

**Клиент**:
```python
context = ssl.create_default_context()
context.check_hostname = True
context.verify_mode = ssl.CERT_REQUIRED
context.load_verify_locations("ca-cert.pem")

sock = socket.socket()
sock = context.wrap_socket(sock, server_hostname=self.host)
sock.connect((self.host, self.port))
```

---

### 4️⃣ SEC-004: Утечка токенов в auth_tokens.json

**Текущий код** (app/client.py строки 2289-2303):
```python
def _save_tokens(self, data=None):
    with open(self.auth_tokens_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)  # ОТКРЫТЫЙ ТЕКСТ!
```

**Решение**: Использовать Windows Credential Manager

```python
import keyring
import keyring.backends.windows

keyring.set_keyring(keyring.backends.windows.WinVaultKeyring())

def _save_tokens(self, data=None):
    if data is None:
        data = self.auth_tokens
    serialized = json.dumps(data)
    keyring.set_password("maxutko", self.username, serialized)

def _load_saved_tokens(self):
    cred = keyring.get_password("maxutko", self.username)
    if cred:
        return json.loads(cred)
    return {"accounts": [], "active": ""}
```

---

### 5️⃣ SEC-005: Бесконечная рекурсия в display_message()

**Текущий код** (app/client.py строки 3500-3512):
```python
def display_message(self, data):
    if data.get("type") == "history":
        items = data.get("items", [])
        for item in items:
            if isinstance(item, dict):
                self.display_message(item)  # БЕЗ ЛИМИТА!
        return
```

**Атака**: Сервер отправит `{"type": "history", "items": [{"type": "history", "items": [...]}]}` глубиной 10000 → Stack Overflow

**Решение**:
```python
def display_message(self, data, depth=0):
    MAX_DEPTH = 5
    
    if depth > MAX_DEPTH:
        print(f"[!] Message depth exceeded: {depth}")
        return
    
    if data.get("type") == "history":
        items = data.get("items", [])
        for item in items:
            if isinstance(item, dict):
                self.display_message(item, depth + 1)
        return
```

---

### 6️⃣ SEC-006: Непредсказуемый passcode

**Текущий код** (server.py строки 434-441):
```python
def _build_passcode(self, username, password, timestamp):
    pw_hash = self._hash_plain(password)
    user_hash = self._hash_plain(username)
    ts_hash = self._hash_plain(timestamp)
    combined = (int(pw_hash, 16) + int(user_hash, 16)) * int(ts_hash, 16)
    return format(combined, "x")
```

**Проблема**: Timestamp известен в +/- 1 секунда. Attacker может:
1. Найти когда был запрос (делает запрос в том же диапазоне)
2. Перебрать hash(password) по топ-1000 паролей
3. Вычислить passcode

**Решение**:
```python
def _build_passcode(self, username):
    # Генерируем случайный passcode
    passcode_bytes = secrets.token_bytes(32)  # 256 бит
    passcode_hex = passcode_bytes.hex()  # 64 символа
    
    # Хешируем для сохранения
    passcode_hash = self._hash_plain(passcode_hex)
    
    return passcode_hex, passcode_hash
```

---

### 7️⃣ SEC-007: Обновления без подписи

**Текущий код** (app/client.py строки 383-422):
```python
urllib.request.urlretrieve(self.download_url, target_path, download_hook)
# Нет проверки подписи!
```

**Атака**: MITM компрометирует Google Drive или DNS → устанавливает вредонос

**Решение** (использовать PyCA/cryptography):
```python
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
import base64

PUBLIC_KEY_PEM = b"""-----BEGIN PUBLIC KEY-----
MIIBIjANBgkqhkiG9...  # RSA-4096 публичный ключ
-----END PUBLIC KEY-----"""

public_key = serialization.load_pem_public_key(PUBLIC_KEY_PEM)

# После скачивания файла:
def verify_update(exe_path, signature_b64):
    signature = base64.b64decode(signature_b64)
    with open(exe_path, 'rb') as f:
        exe_data = f.read()
    
    try:
        public_key.verify(
            signature,
            exe_data,
            padding.PSS(
                mgf=padding.MGF1(hashes.SHA256()),
                salt_length=padding.PSS.MAX_LENGTH
            ),
            hashes.SHA256()
        )
        print("[+] Update signature valid!")
        return True
    except Exception as e:
        print(f"[-] Invalid signature: {e}")
        return False
```

---

## 🟠 HIGH PRIORITY (следующая волна)

### 8️⃣ SEC-008: Слабая защита от брутфорса

**Текущий код** (server.py строки 107-114):
```python
self.bruteforce_threshold = 10  # requests per second
self.bruteforce_window = 1  # second
self.bruteforce_block_duration = 60  # seconds
```

**Проблема**: 
- Блокировка по IP легко обходится (VPN, botnet с разных IP)
- 10 попыток - может быть много для леджитного пользователя

**Решение**:
```python
def _check_bruteforce(self, ip, username=""):
    """Защита от брутфорса с exponential backoff"""
    now = time.time()
    
    # Ключ комбинирует IP + username
    key = f"bruteforce:{ip}:{username}" if username else f"bruteforce:{ip}"
    
    with self.bruteforce_blocked_lock:
        blocked_info = self.bruteforce_blocked_ips.get(key)
        if blocked_info:
            unblock_time = blocked_info["unblock_at"]
            attempts = blocked_info["attempts"]
            
            if now < unblock_time:
                return True  # IP заблокирован
            else:
                del self.bruteforce_blocked_ips[key]
    
    # Tracking по rate limit
    limit_key = f"auth:bruteforce:{ip}:{username}"
    if not self._rate_limit(limit_key, 5, 60):  # 5 попыток в минуту
        # Exponential backoff: 1 мин → 5 мин → 30 мин
        block_duration = min(1800, 60 * (2 ** blocked_info["attempts"]))
        
        with self.bruteforce_blocked_lock:
            self.bruteforce_blocked_ips[key] = {
                "unblock_at": now + block_duration,
                "attempts": (blocked_info.get("attempts", 1) + 1)
            }
        return True
    
    return False
```

---

## 📊 МАТРИЦА РИСКОВ

```
         ↑ Критичность
         |
    CRITICAL |  SEC-001  SEC-002  SEC-003
         |  SEC-004  SEC-005  SEC-006
         |  SEC-007
         |
      HIGH |  SEC-008 through SEC-015
         |
    MEDIUM |  SEC-016 through SEC-030
         |
    ────────────────────────────────────→ Вероятность
         Low              High
```

---

## ✅ ПЛАН ИСПРАВЛЕНИЯ (Приоритет)

### PHASE 1 (WEEK 1) - ОБЯЗАТЕЛЬНО:
- [ ] SEC-003: Включить TLS 1.3
- [ ] SEC-004: Переместить токены в Credential Manager
- [ ] SEC-001: Заменить SHA256 на Argon2
- [ ] SEC-005: Добавить лимит рекурсии

### PHASE 2 (WEEK 2):
- [ ] SEC-002: Шифровать tokens.json
- [ ] SEC-006: Генерировать безопасный passcode
- [ ] SEC-007: Добавить подпись на обновления
- [ ] SEC-008: Улучшить брутфорс защиту

### PHASE 3 (WEEK 3):
- [ ] SEC-009 to SEC-015: Rate limiting, логирование, валидация
- [ ] SEC-016 to SEC-020: Очистка памяти, timeout, E2E (опционально)

### PHASE 4 (ONGOING):
- [ ] SEC-021 to SEC-030: Улучшения безопасности, code review

---

## 🔗 СТАНДАРТЫ И REFERENCES

- **OWASP Top 10**: A02:2021 - Cryptographic Failures, A04:2021 - Insecure Design
- **CWE**: CWE-327, CWE-312, CWE-295, CWE-294, CWE-674, CWE-330, CWE-307, CWE-347
- **CVSS v3.1**: Common Vulnerability Scoring System
- **NIST**: SP 800-63 (Digital Identity Guidelines)
- **Password Hashing**: OWASP Password Storage Cheat Sheet

---

## 📞 КОНТАКТЫ И ПОДДЕРЖКА

Для вопросов по безопасности: создайте issue с меткой `security`

**Автор аудита**: Security Assessment (10.02.2026)  
**Следующая переоценка**: 10.05.2026
