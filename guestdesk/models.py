from __future__ import annotations
from datetime import datetime
from sqlalchemy.orm import declarative_base, relationship
from sqlalchemy import Column, Integer, String, Text, DateTime, ForeignKey

Base = declarative_base()

class Service(Base):
    __tablename__ = 'services'
    id = Column(Integer, primary_key=True)
    name = Column(String(120), nullable=False)
    category = Column(String(64), nullable=False) # Food, Showers, Laundry, Mail, ID, Medical, Legal, Employment, Transport, Other
    description = Column(Text, nullable=True)
    location = Column(String(120), nullable=True)
    contact = Column(String(120), nullable=True)
    schedule_note = Column(String(200), nullable=True)
    external_link = Column(String(200), nullable=True)
    slots = relationship("ProgramSlot", back_populates="service", cascade="all, delete-orphan", order_by="ProgramSlot.dow")

class ProgramSlot(Base):
    __tablename__ = 'program_slots'
    id = Column(Integer, primary_key=True)
    service_id = Column(Integer, ForeignKey('services.id', ondelete="CASCADE"), index=True, nullable=False)
    dow = Column(Integer, nullable=False) # 0=Mon...6=Sun
    start = Column(String(5), nullable=True) # "09:00"
    end = Column(String(5), nullable=True)   # "11:30"
    note = Column(String(200), nullable=True)

    service = relationship("Service", back_populates="slots")

class Announcement(Base):
    __tablename__ = 'announcements'
    id = Column(Integer, primary_key=True)
    title = Column(String(200), nullable=False)
    body = Column(Text, nullable=False)
    starts_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    ends_at = Column(DateTime, nullable=True)

class Submission(Base):
    __tablename__ = 'submissions'
    id = Column(Integer, primary_key=True)
    kind = Column(String(32), nullable=False) # maintenance, grievance, suggestion, question
    subject = Column(String(200), nullable=True)
    body = Column(Text, nullable=False)
    category = Column(String(64), nullable=True) # for maintenance type
    building = Column(String(120), nullable=True)
    location = Column(String(120), nullable=True)
    contact_name = Column(String(120), nullable=True)
    contact_info = Column(String(120), nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    status = Column(String(16), nullable=False, default='new')
