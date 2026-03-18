from datetime import datetime
from sqlalchemy import Column, Integer, Text, DateTime
from app.database import Base


class TextEntry(Base):
    __tablename__ = "texts"

    id = Column(Integer, primary_key=True, index=True)
    title = Column(Text, nullable=False)
    author = Column(Text, nullable=True)
    language = Column(Text, nullable=False)  # "es" or "it"
    raw_text = Column(Text, nullable=False)
    parsed_json = Column(Text, nullable=False)  # JSON blob (token array)
    slug = Column(Text, nullable=True, unique=True)  # URL-safe slug
    word_count = Column(Integer, nullable=True)
    published_at = Column(DateTime, nullable=True)
    edited_tokens = Column(Text, nullable=True)  # JSON object: {token_idx: {field: value}}
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
