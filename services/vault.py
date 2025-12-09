import os
from dotenv import load_dotenv
from cryptography.fernet import Fernet, InvalidToken

load_dotenv()
_key = os.getenv("SMARTDOOR_VAULT_KEY", "").encode()

if not _key:
    raise RuntimeError("SMARTDOOR_VAULT_KEY is missing in .env")

_fernet = Fernet(_key)

def enc(plaintext: str) -> bytes:
    return _fernet.encrypt(plaintext.encode())

def dec(token: bytes) -> str:
    try:
        return _fernet.decrypt(token).decode()
    except InvalidToken:
        return ""
