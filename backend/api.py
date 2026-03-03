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
from app.services.cleaner import ContentCleaner
from app.services.memory import MemoryService
from app.services.database import DatabaseService
from app.services.briefing import BriefingEngine

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

class ChatMessageItem(BaseModel):
    role: str
    content: str

class ChatRequest(BaseModel):
    query: str
    chat_history: Optional[List[ChatMessageItem]] = []

class SourceItem(BaseModel):
    id: str
    subject: str
    snippet: str

class ChatResponse(BaseModel):
    answer: str
    sources: List[SourceItem]

import asyncio
from contextlib import asynccontextmanager

# --- Background Task ---
async def periodic_sync_task():
    """
    Runs the synchronization job periodically in the background.
    """
    logger.info("Starting background sync scheduler...")
    while True:
        try:
            logger.info("Executing scheduled background sync...")
            result = await run_sync_job()
            logger.info(f"Background sync complete. Synced {result.get('synced')} new emails.")
        except Exception as e:
            logger.error(f"Error in background sync: {e}")
        
        # Run every 15 minutes (900 seconds)
        sync_interval = getattr(Config, 'SYNC_INTERVAL_SECONDS', 900)
        await asyncio.sleep(sync_interval)

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: Start the background sync task
    sync_task = asyncio.create_task(periodic_sync_task())
    yield
    # Shutdown: Clean up the task
    sync_task.cancel()
    try:
        await sync_task
    except asyncio.CancelledError:
        logger.info("Background sync scheduler cancelled on shutdown.")

# --- App Init ---
app = FastAPI(
    title="FeedFlux API",
    description="Backend API for FeedFlux Intelligent Email Aggregator",
    version="1.0.0",
    lifespan=lifespan
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
async def get_feed(limit: int = 5, q: Optional[str] = None):
    """
    Fetches emails from the local database, with optional keyword search.
    """
    try:
        # Get emails from database
        emails = db.get_emails(limit=limit, search_query=q)
        
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

@app.get("/api/briefing")
async def get_daily_briefing():
    """
    Returns an AI-generated daily briefing summarizing recent emails.
    """
    try:
        engine = BriefingEngine()
        # To avoid blocking the event loop on LLM generation, we run the synchronous
        # model generation in a threadpool
        briefing_text = await asyncio.to_thread(engine.generate_daily_briefing)
        
        # If the backend returns our default fallback string because of missing keys or errors
        if "AI service is not configured" in briefing_text or "encountered an error" in briefing_text:
             return {"briefing": "", "error": briefing_text}
             
        return {"briefing": briefing_text}
    except Exception as e:
        logger.error(f"Briefing generation failed: {e}")
        # Return a graceful fallback instead of an HTTP 500 so the frontend banner handles it elegantly
        return {"briefing": "", "error": "Daily Briefing is currently unavailable due to high AI service demand or API key issues."}

async def run_sync_job():
    """
    Core synchronization logic.
    Fetches new emails, saves them to SQLite, and indexes them in ChromaDB.
    """
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
        
    return {"synced": synced_count}


@app.post("/api/sync")
async def sync_emails():
    """
    Manually triggers synchronization with Outlook.
    """
    try:
        result = await run_sync_job()
        logger.info(f"Manual sync requested. Synced {result['synced']} emails.")
        return result
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

@app.post("/api/chat", response_model=ChatResponse)
async def chat_with_inbox(request: ChatRequest):
    """
    RAG-enabled endpoint to answer user questions based on their email history.
    """
    try:
        summarizer = ContentSummarizer()
        memory = MemoryService()
        
        result = summarizer.answer_question(request.query, memory, chat_history=request.chat_history)
        
        return ChatResponse(
            answer=result["answer"],
            sources=[SourceItem(**source) for source in result["sources"]]
        )
    except Exception as e:
        logger.error(f"Chat failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))
