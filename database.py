import os
from datetime import datetime

from sqlalchemy import create_engine, Column, Integer, BigInteger, String, Boolean, DateTime, ForeignKey
from sqlalchemy.orm import sessionmaker, declarative_base

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./game_platform.db")

connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}
engine = create_engine(DATABASE_URL, connect_args=connect_args)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


class User(Base):
    __tablename__ = "users"

    id = Column(BigInteger, primary_key=True, index=True)  # Telegram user id
    first_name = Column(String, default="Gamer")
    username = Column(String, nullable=True)

    coins = Column(Integer, default=1000)
    gems = Column(Integer, default=0)

    is_admin = Column(Boolean, default=False)
    is_banned = Column(Boolean, default=False)

    card_back_skin = Column(String, default="classic")
    avatar_skin = Column(String, default="default")

    last_daily_claim = Column(DateTime, nullable=True)
    referred_by = Column(BigInteger, nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow)


class Transaction(Base):
    __tablename__ = "transactions"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(BigInteger, ForeignKey("users.id"), index=True)
    amount = Column(Integer)
    type = Column(String)  # DAILY_REWARD, REFERRAL, WHEEL, GAME_WIN, ...
    created_at = Column(DateTime, default=datetime.utcnow)


def init_db():
    Base.metadata.create_all(bind=engine)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
