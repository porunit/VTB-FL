"""
Подключение к БД (SQLAlchemy 2.0).

DATABASE_URL задаёт движок:
  - прод:   postgresql+psycopg2://user:pass@host:5432/vtbfl
  - локаль: sqlite:///./vtbfl.db   (по умолчанию, для тестов и оффлайн-миграции)

Модели в models.py наследуют Base отсюда. Типы выбраны портируемо (Numeric, Enum,
Date, DateTime) — одна и та же схема поднимается и в Postgres, и в SQLite.
"""

import os

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

DATABASE_URL = os.environ.get('DATABASE_URL', 'sqlite:///./vtbfl.db')

_connect_args = {'check_same_thread': False} if DATABASE_URL.startswith('sqlite') else {}
engine = create_engine(DATABASE_URL, future=True, connect_args=_connect_args)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, future=True)


class Base(DeclarativeBase):
    pass


def get_session():
    return SessionLocal()
