import uuid
from datetime import datetime

from sqlalchemy import Column, String, Text, Boolean, DateTime, ForeignKey
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.db.session import Base


class Conversation(Base):
    __tablename__ = "conversations"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    instagram_user_id = Column(String, nullable=False, index=True)
    state = Column(String, nullable=False, default="active")
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

    messages = relationship("Message", back_populates="conversation", order_by="Message.created_at")
    profile = relationship("ProspectProfile", back_populates="conversation", uselist=False)


class Message(Base):
    __tablename__ = "messages"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    conversation_id = Column(UUID(as_uuid=True), ForeignKey("conversations.id"), nullable=False)
    role = Column(String, nullable=False)
    content = Column(Text, nullable=False)
    instagram_message_id = Column(String, nullable=True, unique=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    conversation = relationship("Conversation", back_populates="messages")


class ProspectProfile(Base):
    __tablename__ = "prospect_profiles"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    conversation_id = Column(UUID(as_uuid=True), ForeignKey("conversations.id"), nullable=False)
    nombre = Column(String, nullable=True)
    empresa = Column(String, nullable=True)
    sector = Column(String, nullable=True)
    necesidad_principal = Column(Text, nullable=True)
    presencia_digital = Column(String, nullable=True)
    tiene_identidad_marca = Column(String, nullable=True)
    objetivo_principal = Column(Text, nullable=True)
    presupuesto_aprox = Column(String, nullable=True)
    telefono = Column(String, nullable=True)
    sheets_synced = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    conversation = relationship("Conversation", back_populates="profile")
