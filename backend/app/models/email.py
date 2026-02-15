from sqlalchemy import Column, String, Text, Boolean, DateTime, JSON
from sqlalchemy.ext.declarative import declarative_base
from datetime import datetime

Base = declarative_base()

class Email(Base):
    """Email model for persistent storage"""
    __tablename__ = 'emails'
    
    # Primary key
    id = Column(String, primary_key=True)
    
    # Basic information
    subject = Column(Text, nullable=False)
    sender_name = Column(String)
    sender_email = Column(String, nullable=False)
    received_datetime = Column(DateTime, nullable=False)
    
    # Content
    body_preview = Column(Text)
    body_content = Column(Text)
    body_html = Column(Text)
    
    # Generated content
    summary = Column(Text)
    summary_model = Column(String)  # AI model used for summary generation
    summary_generated_at = Column(DateTime)  # When the summary was generated
    
    # Status
    is_read = Column(Boolean, default=False)
    is_starred = Column(Boolean, default=False)
    is_archived = Column(Boolean, default=False)
    
    # Email specific
    has_attachments = Column(Boolean, default=False)
    attachments = Column(JSON)
    
    # Extensibility
    metadata_json = Column('metadata', JSON)  # Reserved for future extensions
    
    # Timestamps
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    def __repr__(self):
        return f"<Email(id='{self.id}', subject='{self.subject}')>"
