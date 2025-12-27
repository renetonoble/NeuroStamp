from sqlalchemy import create_engine, Column, Integer, String, LargeBinary
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from cryptography.fernet import Fernet
import json
import os

# 1. DATABASE SETUP
SQLALCHEMY_DATABASE_URL = "sqlite:///./neurostamp.db"
engine = create_engine(SQLALCHEMY_DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

# 2. ENCRYPTION SETUP
# We generate a key file so the encryption stays consistent across restarts.
KEY_FILE = "secret.key"

def load_key():
    if not os.path.exists(KEY_FILE):
        key = Fernet.generate_key()
        with open(KEY_FILE, "wb") as key_file:
            key_file.write(key)
    else:
        with open(KEY_FILE, "rb") as key_file:
            key = key_file.read()
    return key

CIPHER_SUITE = Fernet(load_key())

# 3. MODELS

class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True, index=True)
    username = Column(String, unique=True, index=True) # Login Name
    user_uid = Column(String, unique=True, index=True) # PUBLIC ID (Random UUID)
    
    # STORED AS ENCRYPTED BYTES (Hackers see garbage data here)
    encrypted_key_data = Column(LargeBinary, nullable=True) 

    def set_key_data(self, data_list):
        """Encrypts list of coefficients before storing"""
        if data_list is None: return
        json_str = json.dumps(data_list)
        # Encrypt the string to bytes
        self.encrypted_key_data = CIPHER_SUITE.encrypt(json_str.encode())

    def get_key_data(self):
        """Decrypts data back to list"""
        if not self.encrypted_key_data: return None
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
    owner_uid = Column(String) # Stores the UUID, not the name

def init_db():
    Base.metadata.create_all(bind=engine)