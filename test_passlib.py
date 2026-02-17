from passlib.context import CryptContext

pwd_context = CryptContext(schemes=["pbkdf2_sha256"], deprecated="auto")

try:
    password = "password123"
    print(f"Hashing password: '{password}' (len: {len(password)})")
    hash = pwd_context.hash(password)
    print(f"Success: {hash}")
except Exception as e:
    print(f"FAILED: {e}")
