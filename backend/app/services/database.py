import logging
import os
from pathlib import Path
from sqlalchemy import create_engine, desc
from sqlalchemy.orm import sessionmaker
from app.models.email import Base, Email
from datetime import datetime

logger = logging.getLogger(__name__)

# Get project root directory (4 levels up from this file)
PROJECT_ROOT = Path(__file__).parent.parent.parent.parent
DATA_DIR = PROJECT_ROOT / "data"

class DatabaseService:
    """Service for database operations"""
    
    def __init__(self, db_path: str = None):
        """Initialize database connection and create tables"""
        if db_path is None:
            # Default to project root data directory
            DATA_DIR.mkdir(exist_ok=True)
            db_path = str(DATA_DIR / "emails.db")
        
        self.engine = create_engine(f'sqlite:///{db_path}', echo=False)
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine)
        logger.info(f"Database initialized at {db_path}")
    
    def insert_email(self, email_data: dict):
        """Insert or update email (upsert)"""
        session = self.Session()
        try:
            # Check if email exists
            existing = session.query(Email).filter_by(id=email_data['id']).first()
            
            if existing:
                # Update existing email
                for key, value in email_data.items():
                    if hasattr(existing, key):
                        setattr(existing, key, value)
                existing.updated_at = datetime.utcnow()
                logger.debug(f"Updated email: {email_data['id']}")
            else:
                # Insert new email
                email = Email(**email_data)
                session.add(email)
                logger.debug(f"Inserted email: {email_data['id']}")
            
            session.commit()
        except Exception as e:
            session.rollback()
            logger.error(f"Failed to insert/update email: {e}")
            raise
        finally:
            session.close()
    
    def get_emails(self, limit: int = 20, offset: int = 0, unread_only: bool = False):
        """Get emails list with pagination"""
        session = self.Session()
        try:
            query = session.query(Email)
            
            if unread_only:
                query = query.filter(Email.is_read == False)
            
            emails = query.order_by(desc(Email.received_datetime))\
                         .limit(limit)\
                         .offset(offset)\
                         .all()
            
            return [self._email_to_dict(e) for e in emails]
        finally:
            session.close()
    
    def get_email_by_id(self, email_id: str):
        """Get single email by ID"""
        session = self.Session()
        try:
            email = session.query(Email).filter_by(id=email_id).first()
            return self._email_to_dict(email) if email else None
        finally:
            session.close()
    
    def update_email_status(self, email_id: str, **kwargs):
        """Update email status fields"""
        session = self.Session()
        try:
            email = session.query(Email).filter_by(id=email_id).first()
            if email:
                for key, value in kwargs.items():
                    if hasattr(email, key):
                        setattr(email, key, value)
                email.updated_at = datetime.utcnow()
                session.commit()
                logger.debug(f"Updated email status: {email_id}")
        except Exception as e:
            session.rollback()
            logger.error(f"Failed to update email status: {e}")
            raise
        finally:
            session.close()
    
    def get_email_count(self):
        """Get total email count"""
        session = self.Session()
        try:
            return session.query(Email).count()
        finally:
            session.close()
    
    def _email_to_dict(self, email):
        """Convert Email model to dictionary"""
        if not email:
            return None
        
        return {
            "id": email.id,
            "subject": email.subject,
            "sender": email.sender_name,
            "sender_email": email.sender_email,
            "received_datetime": email.received_datetime,  # Unix timestamp
            "body_preview": email.body_preview,
            "body_content": email.body_content,
            "body_html": email.body_html,
            "summary": email.summary,
            "summary_model": email.summary_model,
            "summary_generated_at": email.summary_generated_at,  # Unix timestamp
            "is_read": email.is_read,
            "is_starred": email.is_starred,
            "is_archived": email.is_archived,
            "has_attachments": email.has_attachments,
            "attachments": email.attachments,
        }
