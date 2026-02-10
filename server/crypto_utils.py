import hashlib
import secrets
import string

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
    return legacy_hash_password(password, salt) == stored_hash


def random_digits(length=10):
    if length <= 0:
        return ""
    digits = string.digits
    return "".join(secrets.choice(digits) for _ in range(length))


def build_passcode(username, password):
    if not username or not password:
        return ""
    nonce = random_digits(10)
    pw_hash = sha256_hex(password)
    user_hash = sha256_hex(username)
    nonce_hash = sha256_hex(nonce)
    try:
        combined = (int(pw_hash, 16) + int(user_hash, 16)) * int(nonce_hash, 16)
    except Exception:
        return ""
    return format(combined, "x")
