from sqlalchemy import create_engine, Column, Integer, String, PickleType
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker

# 1. Database Setup
SQLALCHEMY_DATABASE_URL = "sqlite:///./neurostamp.db"

engine = create_engine(
    SQLALCHEMY_DATABASE_URL, connect_args={"check_same_thread": False}
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()

# 2. User Table (Stores credentials and keys)
class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True, index=True)
    username = Column(String, unique=True, index=True)
    secret_key_data = Column(PickleType) # Stores the watermark key

# 3. Image Registry Table (PREVENTS DOUBLE SPENDING)
# This stores the visual fingerprint of every stamped image.
class ImageRegistry(Base):
    __tablename__ = "image_registry"
    
    id = Column(Integer, primary_key=True, index=True)
    image_hash = Column(String, unique=True, index=True) # The Perceptual Hash (Fingerprint)
    owner = Column(String) # The original owner

def init_db():
    Base.metadata.create_all(bind=engine)