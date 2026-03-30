from sqlalchemy import create_engine, Column, Integer, String, LargeBinary
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
# AES-256-GCM — matches the PPT claim of "military-grade AES-256" encryption.
# Fernet (used previously) is AES-128-CBC+HMAC; AESGCM with a 32-byte key is true AES-256.
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
import json
import os
import secrets
import urllib.parse as _urlparse

# ============================================================
# 1. DATABASE SETUP
# ============================================================
# Set DATABASE_URL env var to a PostgreSQL connection string for cloud hosting.
# Falls back to local SQLite for development when the env var is not set.

DATABASE_URL = os.environ.get("DATABASE_URL", "sqlite:///./neurostamp.db")

# Render / some providers serve "postgres://" (legacy) — SQLAlchemy needs "postgresql://"
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

# Use psycopg3 dialect if available (works with Python 3.10+)
if DATABASE_URL.startswith("postgresql://") or DATABASE_URL.startswith("postgresql+psycopg2://"):
    DATABASE_URL = DATABASE_URL.replace("postgresql://", "postgresql+psycopg://", 1)
    DATABASE_URL = DATABASE_URL.replace("postgresql+psycopg2://", "postgresql+psycopg://", 1)

# Strip query params that psycopg3 doesn't support (e.g., Neon's channel_binding=require)
_UNSUPPORTED_PARAMS = {"channel_binding"}
if DATABASE_URL.startswith("postgresql"):
    _parsed = _urlparse.urlparse(DATABASE_URL)
    _qs = {k: v for k, v in _urlparse.parse_qsl(_parsed.query) if k not in _UNSUPPORTED_PARAMS}
    DATABASE_URL = _urlparse.urlunparse(_parsed._replace(query=_urlparse.urlencode(_qs)))

IS_POSTGRES = DATABASE_URL.startswith("postgresql")

if IS_POSTGRES:
    engine = create_engine(
        DATABASE_URL,
        pool_pre_ping=True,
        pool_size=5,
        max_overflow=10,
    )
else:
    # SQLite-specific: disable same-thread check for FastAPI's threading model
    engine = create_engine(
        DATABASE_URL,
        connect_args={"check_same_thread": False},
    )

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

# 2. ENCRYPTION SETUP (For Watermark Keys)
# Uses AES-256-GCM:
#   - 256-bit (32-byte) key
#   - 96-bit (12-byte) random nonce generated fresh per encryption
#   - Built-in authentication tag (GCM) — detects tampering automatically
# Key layout on disk / env:
#   ENV  : NEUROSTAMP_SECRET_KEY as a 64-character lowercase hex string (32 bytes)
#   FILE : secret.key — 32 raw bytes (binary)
# NOTE: If upgrading from a previous Fernet-based installation, delete secret.key
#       and re-stamp images; old encrypted key data will not be compatible.
KEY_FILE = "secret.key"

import warnings

def load_key() -> bytes:
    """Load the 32-byte AES-256 key from env var or key file."""
    # Priority 1: Environment variable (recommended for production)
    env_key = os.environ.get("NEUROSTAMP_SECRET_KEY")
    if env_key:
        try:
            key_bytes = bytes.fromhex(env_key)  # expect 64-char hex
        except ValueError:
            key_bytes = env_key.encode()  # fallback: treat as raw bytes
        if len(key_bytes) != 32:
            raise ValueError(
                f"NEUROSTAMP_SECRET_KEY must decode to exactly 32 bytes (256 bits). "
                f"Got {len(key_bytes)} bytes. Use a 64-char hex string."
            )
        return key_bytes

    # Priority 2: Key file (dev/local only)
    warnings.warn(
        "NEUROSTAMP_SECRET_KEY env var not set — falling back to file-based key. "
        "This is insecure for production. Set the env var to a 64-char hex AES-256 key.",
        stacklevel=2,
    )
    if not os.path.exists(KEY_FILE):
        key = secrets.token_bytes(32)  # 256-bit random key
        with open(KEY_FILE, "wb") as key_file:
            key_file.write(key)
        print(f"[KEY] Generated new AES-256 key and saved to '{KEY_FILE}'.")
    else:
        with open(KEY_FILE, "rb") as key_file:
            key = key_file.read()
        if len(key) != 32:
            raise ValueError(
                f"'{KEY_FILE}' is not a valid 32-byte AES-256 key (got {len(key)} bytes). "
                "Delete it to auto-generate a new one."
            )
    return key

# Module-level cipher key (loaded once at startup)
_AES_KEY = load_key()

def _encrypt(plaintext: bytes) -> bytes:
    """Encrypt bytes with AES-256-GCM. Returns nonce (12 B) + ciphertext."""
    nonce = secrets.token_bytes(12)  # 96-bit nonce — must be unique per encryption
    ct = AESGCM(_AES_KEY).encrypt(nonce, plaintext, None)
    return nonce + ct  # prepend nonce so decryption can recover it

def _decrypt(blob: bytes) -> bytes:
    """Decrypt AES-256-GCM blob (nonce + ciphertext). Raises on tamper."""
    if len(blob) < 12:
        raise ValueError("Ciphertext blob too short to contain a nonce.")
    nonce, ct = blob[:12], blob[12:]
    return AESGCM(_AES_KEY).decrypt(nonce, ct, None)

# 3. MODELS

class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True, index=True)
    username = Column(String, unique=True, index=True)
    hashed_password = Column(String)  # <--- NEW: Stores the password hash
    user_uid = Column(String, unique=True, index=True)
    
    # STORED AS ENCRYPTED BYTES
    encrypted_key_data = Column(LargeBinary, nullable=True) 

    def set_key_data(self, data_list):
        """Encrypt and store the watermark key (list of S[0] floats) using AES-256-GCM."""
        if data_list is None:
            return
        json_str = json.dumps(data_list)
        self.encrypted_key_data = _encrypt(json_str.encode())

    def get_key_data(self):
        """Decrypt and return the watermark key, or None on failure."""
        if not self.encrypted_key_data:
            return None
        try:
            decrypted_json = _decrypt(bytes(self.encrypted_key_data)).decode()
            return json.loads(decrypted_json)
        except Exception as e:
            print(f"[AES-256-GCM Decryption Error] {e}")
            return None

class ImageRegistry(Base):
    __tablename__ = "image_registry"
    id = Column(Integer, primary_key=True, index=True)
    image_hash = Column(String, unique=True, index=True)
    owner_uid = Column(String)
    original_width = Column(Integer, default=0)
    original_height = Column(Integer, default=0)

def init_db():
    Base.metadata.create_all(bind=engine)