import os
from datetime import datetime
from sqlalchemy import create_engine, Column, Integer, String, BigInteger, Boolean, DateTime, ForeignKey
from sqlalchemy.orm import declarative_base, sessionmaker

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./game_hub.db")

if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

engine = create_engine(
    DATABASE_URL, 
    connect_args={"check_same_thread": False} if "sqlite" in DATABASE_URL else {}
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

class User(Base):
    __tablename__ = "users"

    id = Column(BigInteger, primary_key=True, index=True)
    first_name = Column(String, default="Gamer")
    username = Column(String, nullable=True)
    coins = Column(Integer, default=1000)
    gems = Column(Integer, default=10)
    avatar_skin = Column(String, default="default")
    card_back_skin = Column(String, default="classic")
    is_admin = Column(Boolean, default=False)
    is_banned = Column(Boolean, default=False)
    referred_by = Column(BigInteger, nullable=True)
    last_daily_claim = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

class Transaction(Base):
    __tablename__ = "transactions"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(BigInteger, ForeignKey("users.id"))
    amount = Column(Integer)
    type = Column(String)
    created_at = Column(DateTime, default=datetime.utcnow)

Base.metadata.create_all(bind=engine)

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
