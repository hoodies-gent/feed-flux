from sqlalchemy import Column, String, Text, Boolean, Integer, JSON
from sqlalchemy.ext.declarative import declarative_base
from datetime import datetime

Base = declarative_base()


class SentAction(Base):
    """Dry-run record of an outbound reply the agent proposed and the user approved.

    Phase 1 constraint: FeedFlux never POSTs writes back to Microsoft Graph.
    Every row here is an approved-but-not-actually-sent email.
    """
    __tablename__ = 'sent_actions'

    id = Column(Integer, primary_key=True, autoincrement=True)
    thread_id = Column(String, nullable=False)  # agent conversation id
    original_email_id = Column(String)  # nullable — send may not reference a specific email
    recipient = Column(String, nullable=False)
    subject = Column(Text, nullable=False)
    body = Column(Text, nullable=False)
    approved_at = Column(Integer, nullable=False,
                         default=lambda: int(datetime.utcnow().timestamp()))


class Email(Base):
    """Email model for persistent storage"""
    __tablename__ = 'emails'
    
    # Primary key
    id = Column(String, primary_key=True)
    
    # Basic information
    subject = Column(Text, nullable=False)
    sender_name = Column(String)
    sender_email = Column(String, nullable=False)
    received_datetime = Column(Integer, nullable=False)  # Unix timestamp
    
    # Content
    body_preview = Column(Text)
    body_content = Column(Text)
    body_html = Column(Text)
    
    # Generated content
    summary = Column(Text)
    summary_model = Column(String)  # AI model used for summary generation
    summary_generated_at = Column(Integer)  # Unix timestamp
    
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
    created_at = Column(Integer, default=lambda: int(datetime.utcnow().timestamp()))
    updated_at = Column(Integer, default=lambda: int(datetime.utcnow().timestamp()), 
                        onupdate=lambda: int(datetime.utcnow().timestamp()))
    
    def __repr__(self):
        return f"<Email(id='{self.id}', subject='{self.subject}')>"
