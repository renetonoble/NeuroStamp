from sqlalchemy import create_engine, Column, Integer, String, LargeBinary
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from cryptography.fernet import Fernet
import json
import os
import urllib.parse as _urlparse

# ============================================================
# 1. DATABASE URL
# ============================================================
# Set DATABASE_URL env var to a PostgreSQL connection string to use Postgres.
# Falls back to local SQLite for development when the env var is not set.

DATABASE_URL = os.environ.get("DATABASE_URL", "sqlite:///./neurostamp.db")

# Render / some providers serve "postgres://" (legacy) — SQLAlchemy needs "postgresql://"
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

# Use psycopg3 dialect (works with Python 3.13+)
if DATABASE_URL.startswith("postgresql://") or DATABASE_URL.startswith("postgresql+psycopg2://"):
    DATABASE_URL = DATABASE_URL.replace("postgresql://", "postgresql+psycopg://", 1)
    DATABASE_URL = DATABASE_URL.replace("postgresql+psycopg2://", "postgresql+psycopg://", 1)

# Strip query params that psycopg3 doesn't support.
# Neon includes channel_binding=require which causes psycopg3 to crash on startup.
_UNSUPPORTED_PARAMS = {"channel_binding"}
if DATABASE_URL.startswith("postgresql"):
    _parsed = _urlparse.urlparse(DATABASE_URL)
    _qs = {k: v for k, v in _urlparse.parse_qsl(_parsed.query) if k not in _UNSUPPORTED_PARAMS}
    DATABASE_URL = _urlparse.urlunparse(_parsed._replace(query=_urlparse.urlencode(_qs)))

IS_POSTGRES = DATABASE_URL.startswith("postgresql")

# ============================================================
# 2. ENGINE & SESSION
# ============================================================

if IS_POSTGRES:
    # psycopg3 reads sslmode directly from the URL — no extra connect_args needed
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

# ============================================================
# 3. ENCRYPTION SETUP (For Watermark Keys)
# ============================================================

KEY_FILE = "secret.key"

def load_key():
    # Cloud env: read from SECRET_KEY environment variable
    env_key = os.environ.get("SECRET_KEY")
    if env_key:
        try:
            Fernet(env_key.encode())
            return env_key.encode()
        except Exception:
            return Fernet.generate_key()
    # Local dev fallback: persist key in a file so it survives restarts
    if not os.path.exists(KEY_FILE):
        key = Fernet.generate_key()
        with open(KEY_FILE, "wb") as f:
            f.write(key)
    else:
        with open(KEY_FILE, "rb") as f:
            key = f.read()
    return key

CIPHER_SUITE = Fernet(load_key())

# ============================================================
# 4. MODELS
# ============================================================

class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True, index=True)
    username = Column(String, unique=True, index=True)
    hashed_password = Column(String)
    user_uid = Column(String, unique=True, index=True)

    # Watermark key stored encrypted
    encrypted_key_data = Column(LargeBinary, nullable=True)

    def set_key_data(self, data_list):
        if data_list is None:
            return
        json_str = json.dumps(data_list)
        self.encrypted_key_data = CIPHER_SUITE.encrypt(json_str.encode())

    def get_key_data(self):
        if not self.encrypted_key_data:
            return None
        try:
            decrypted_json = CIPHER_SUITE.decrypt(self.encrypted_key_data).decode()
            return json.loads(decrypted_json)
        except Exception as e:
            print(f"Encryption Error: {e}")
            return None


class ImageRegistry(Base):
    __tablename__ = "image_registry"
    id = Column(Integer, primary_key=True, index=True)
    image_hash = Column(String, unique=True, index=True)
    owner_uid = Column(String)
    original_width = Column(Integer, default=0)
    original_height = Column(Integer, default=0)


# ============================================================
# 5. INIT
# ============================================================

def init_db():
    """Create all tables if they don't already exist."""
    Base.metadata.create_all(bind=engine)