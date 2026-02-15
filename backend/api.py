from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import List, Optional
import logging
from app.core.config import Config
from app.services.fetcher import OutlookFetcher
from app.services.summarizer import ContentSummarizer
from app.services.cleaner import ContentCleaner
from app.services.memory import MemoryService
from app.services.database import DatabaseService

# Initialize Database Service
db = DatabaseService()

# Configure logging
logging.basicConfig(level=getattr(logging, Config.LOG_LEVEL))
logger = logging.getLogger("feedflux-api")

# --- Models ---
class FeedItem(BaseModel):
    id: str
    subject: str
    sender: str
    received_datetime: int  # Unix timestamp
    body_preview: str

class SummaryRequest(BaseModel):
    text: str
    email_id: str
    subject: str 

# --- App Init ---
app = FastAPI(
    title="FeedFlux API",
    description="Backend API for FeedFlux Intelligent Email Aggregator",
    version="1.0.0"
)

# --- Middleware ---
origins = [
    "http://localhost:3000",  # Next.js Frontend
    "http://127.0.0.1:3000",
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- Endpoints ---
@app.get("/")
async def health_check():
    return {"status": "ok", "service": "FeedFlux API"}

@app.get("/api/status")
async def app_status():
    """
    Returns generic app status.
    """
    return {
        "env": "development", 
        "rag_enabled": True, 
        "version": "1.0.0"
    }

@app.get("/api/feed", response_model=List[FeedItem])
async def get_feed(limit: int = 5):
    """
    Fetches emails from the local database.
    """
    try:
        # Get emails from database
        emails = db.get_emails(limit=limit)
        
        feed = []
        for email in emails:
            # Map DB fields to FeedItem
            feed.append({
                "id": email["id"],
                "subject": email["subject"],
                "sender": email["sender"] or email["sender_email"],  # Use name or fallback to email
                "received_datetime": email["received_datetime"],
                "body_preview": email["body_preview"]
            })
        return feed
    except Exception as e:
        logger.error(f"Feed fetch failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/sync")
async def sync_emails():
    """
    Manually triggers synchronization with Outlook.
    Fetches new emails, saves them to SQLite, and indexes them in ChromaDB.
    """
    try:
        fetcher = OutlookFetcher()
        # Fetch a batch of emails (limit can be higher for sync)
        new_emails = fetcher.fetch_emails(limit=20)
        
        synced_count = 0
        memory = MemoryService()
        cleaner = ContentCleaner()

        for email in new_emails:
            # Extract content
            body_content = email.get("body", {}).get("content", "")
            body_text = cleaner.clean_html(body_content) if body_content else ""
            
            # Prepare data for Database
            email_data = {
                "id": email["id"],
                "subject": email.get("subject", "No Subject"),
                "sender_name": email.get("from", {}).get("emailAddress", {}).get("name"),
                "sender_email": email.get("from", {}).get("emailAddress", {}).get("address", ""),
                "received_datetime": email.get("receivedDateTime"), 
                "body_preview": email.get("bodyPreview"),
                "body_content": body_text,
                "body_html": body_content,
                "is_read": email.get("isRead", False),
                "has_attachments": email.get("hasAttachments", False),
            }
            
            # Parse datetime string to Unix timestamp for SQLAlchemy
            if isinstance(email_data["received_datetime"], str):
                from datetime import datetime
                # Handle 'Z' replacement for compatibility
                dt_str = email_data["received_datetime"].replace('Z', '+00:00')
                email_data["received_datetime"] = int(datetime.fromisoformat(dt_str).timestamp())


            # 1. Save to Database
            db.insert_email(email_data)
            
            # 2. Index in ChromaDB (RAG)
            # Only index if it has content
            if body_text:
                memory.add_email(
                    email_id=email["id"],
                    text=f"{email_data['subject']}. {body_text}",
                    metadata={
                        "subject": email_data["subject"],
                        "sender": email_data["sender_email"],
                        "received": email["receivedDateTime"], # Keep original string for metadata
                    }
                )
            
            synced_count += 1
            
        logger.info(f"Synced {synced_count} emails.")
        return {"synced": synced_count}
    
    except Exception as e:
        logger.error(f"Sync failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/emails/{email_id}")
async def get_email_detail(email_id: str):
    """
    Retrieves full details of a specific email.
    """
    try:
        email = db.get_email_by_id(email_id)
        if not email:
            raise HTTPException(status_code=404, detail="Email not found")
        return email
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error fetching email detail: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/summarize")
async def generate_summary(request: SummaryRequest):
    """
    Generates a RAG-enhanced summary for a specific email.
    """
    try:
        summarizer = ContentSummarizer()
        memory = MemoryService()
        
        # 1. Retrieve Context
        context_docs = []
        try:
            related = memory.query_related(request.text, n_results=3)
            if related and related['documents']:
                context_docs = related['documents'][0]
        except Exception as e:
            logger.warning(f"Context retrieval failed: {e}")
            # Continue without context if retrieval fails

        # 2. Summarize
        summary = summarizer.summarize(request.text, context_documents=context_docs)
        
        # 3. Save Summary to Database with metadata
        from datetime import datetime
        generated_at = int(datetime.utcnow().timestamp())
        
        db.update_email_status(
            request.email_id, 
            summary=summary,
            summary_model=Config.GEMINI_MODEL_NAME,
            summary_generated_at=generated_at
        )
        
        return {
            "summary": summary,
            "context_count": len(context_docs),
            "model": Config.GEMINI_MODEL_NAME,
            "generated_at": generated_at  # Unix timestamp
        }
    except Exception as e:
        logger.error(f"Summarization failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))
