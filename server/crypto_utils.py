import hashlib
import hmac
import secrets
import string
import os

try:
    from argon2 import PasswordHasher
    from argon2.exceptions import VerifyMismatchError
    _ARGON2_AVAILABLE = True
except Exception:
    PasswordHasher = None
    VerifyMismatchError = None
    _ARGON2_AVAILABLE = False

try:
    import bcrypt
    _BCRYPT_AVAILABLE = True
except Exception:
    bcrypt = None
    _BCRYPT_AVAILABLE = False

try:
    from cryptography.fernet import Fernet, InvalidToken
    _FERNET_AVAILABLE = True
except Exception:
    Fernet = None
    InvalidToken = None
    _FERNET_AVAILABLE = False


class CryptoUnavailableError(RuntimeError):
    pass


_ARGON2_HASHER = None
if _ARGON2_AVAILABLE:
    _ARGON2_HASHER = PasswordHasher(
        time_cost=2,
        memory_cost=65536,
        parallelism=4,
        hash_len=16,
        salt_len=16
    )


def _password_backend():
    if _ARGON2_AVAILABLE:
        return "argon2"
    if _BCRYPT_AVAILABLE:
        return "bcrypt"
    return ""


def is_argon2_hash(value):
    return isinstance(value, str) and value.startswith("$argon2")


def is_bcrypt_hash(value):
    if not isinstance(value, str):
        return False
    return (
        value.startswith("$2a$")
        or value.startswith("$2b$")
        or value.startswith("$2y$")
        or value.startswith("$2$")
    )


def is_modern_password_hash(value):
    return is_argon2_hash(value) or is_bcrypt_hash(value)


def hash_password(password):
    backend = _password_backend()
    if not backend:
        raise CryptoUnavailableError("argon2-cffi or bcrypt is required")
    if backend == "argon2":
        return _ARGON2_HASHER.hash(password)
    pw_bytes = str(password).encode("utf-8")
    return bcrypt.hashpw(pw_bytes, bcrypt.gensalt()).decode("utf-8")


def verify_password_hash(stored_hash, password):
    if not stored_hash:
        return False
    if is_argon2_hash(stored_hash):
        if not _ARGON2_AVAILABLE:
            raise CryptoUnavailableError("argon2-cffi is required to verify this hash")
        try:
            return _ARGON2_HASHER.verify(stored_hash, password)
        except VerifyMismatchError:
            return False
        except Exception:
            return False
    if is_bcrypt_hash(stored_hash):
        if not _BCRYPT_AVAILABLE:
            raise CryptoUnavailableError("bcrypt is required to verify this hash")
        try:
            return bcrypt.checkpw(str(password).encode("utf-8"), stored_hash.encode("utf-8"))
        except Exception:
            return False
    return False


def sha256_hex(value):
    return hashlib.sha256(str(value).encode("utf-8")).hexdigest()


def legacy_hash_password(password, salt):
    return sha256_hex((salt or "") + (password or ""))


def verify_legacy_password(stored_hash, salt, password):
    if not stored_hash or not salt:
        return False
    return hmac.compare_digest(legacy_hash_password(password, salt), stored_hash)


def random_digits(length=10):
    if length <= 0:
        return ""
    digits = string.digits
    return "".join(secrets.choice(digits) for _ in range(length))


def build_passcode(username, password):
    """Генерирует криптографически стойкий случайный PassCode (256 бит энтропии)."""
    if not username or not password:
        return ""
    return secrets.token_hex(32)


def hash_passcode(raw_passcode):
    """Хеширует PassCode перед сохранением в БД сервера."""
    raw_passcode = (raw_passcode or "").strip()
    if not raw_passcode:
        return ""
    return "sha256$" + sha256_hex(raw_passcode)


def verify_passcode(stored_value, raw_passcode):
    """Проверяет PassCode с защитой от timing-атак."""
    stored_value = (stored_value or "").strip()
    raw_passcode = (raw_passcode or "").strip()
    if not stored_value or not raw_passcode:
        return False
    if stored_value.startswith("sha256$"):
        expected = stored_value.split("$", 1)[1]
        actual = sha256_hex(raw_passcode)
        return hmac.compare_digest(expected, actual)
    # Обратная совместимость со старыми записями
    return hmac.compare_digest(stored_value, raw_passcode)


# ================= TOKEN ENCRYPTION =================

_TOKEN_CIPHER = None
_TOKEN_KEY_PATH = None


def _init_token_cipher():
    global _TOKEN_CIPHER, _TOKEN_KEY_PATH
    if not _FERNET_AVAILABLE:
        return False
    try:
        token_key_path = os.environ.get(
            "MAXUTKO_TOKEN_KEY_PATH",
            os.path.join(os.path.dirname(__file__), ".token_key")
        )
        _TOKEN_KEY_PATH = token_key_path
        if os.path.exists(token_key_path):
            with open(token_key_path, "rb") as f:
                key = f.read()
        else:
            key = Fernet.generate_key()
            with open(token_key_path, "wb") as f:
                f.write(key)
            try:
                os.chmod(token_key_path, 0o600)
            except Exception:
                pass
        _TOKEN_CIPHER = Fernet(key)
        return True
    except Exception as e:
        print(f"[!] Failed to init token cipher: {e}")
        return False


def encrypt_token(token_str):
    if not token_str:
        return token_str
    global _TOKEN_CIPHER
    if _TOKEN_CIPHER is None:
        _init_token_cipher()
    if _TOKEN_CIPHER is None:
        return token_str
    try:
        token_bytes = token_str.encode("utf-8")
        encrypted = _TOKEN_CIPHER.encrypt(token_bytes)
        return encrypted.decode("ascii")
    except Exception as e:
        print(f"[!] Token encryption failed: {e}")
        return token_str


def decrypt_token(encrypted_token):
    if not encrypted_token:
        return encrypted_token
    global _TOKEN_CIPHER
    if _TOKEN_CIPHER is None:
        _init_token_cipher()
    if _TOKEN_CIPHER is None:
        return encrypted_token
    try:
        encrypted_bytes = encrypted_token.encode("utf-8")
        decrypted = _TOKEN_CIPHER.decrypt(encrypted_bytes)
        return decrypted.decode("utf-8")
    except (InvalidToken, Exception):
        return encrypted_token