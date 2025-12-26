from sqlalchemy import create_engine, Column, Integer, String, PickleType
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker

# 1. Create SQLite Database File
DATABASE_URL = "sqlite:///./neurostamp.db"

engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()

# 2. Define the "Users" Table
class User(Base):
    __tablename__ = "users"
    
    id = Column(Integer, primary_key=True, index=True)
    username = Column(String, unique=True, index=True)
    # We store the secret key (list of floats) as a binary blob (Pickle)
    secret_key_data = Column(PickleType) 

# 3. Create the Tables
def init_db():
    Base.metadata.create_all(bind=engine)

