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

# Configure logging
logging.basicConfig(level=getattr(logging, Config.LOG_LEVEL))
logger = logging.getLogger("feedflux-api")

# --- Models ---
class FeedItem(BaseModel):
    id: str
    subject: str
    sender: str
    received_datetime: str
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
    Fetches recent emails from Outlook.
    """
    try:
        fetcher = OutlookFetcher()
        emails = fetcher.fetch_emails(limit=limit)
        
        feed = []
        cleaner = ContentCleaner()
        
        for email in emails:
            # Basic parsing for frontend display
            feed.append({
                "id": email.get("id"),
                "subject": email.get("subject", "No Subject"),
                "sender": email.get("from", {}).get("emailAddress", {}).get("name", "Unknown"),
                "received_datetime": email.get("receivedDateTime"),
                "body_preview": email.get("bodyPreview", "")
            })
        return feed
    except Exception as e:
        logger.error(f"Feed fetch failed: {e}")
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

        # 2. Summarize
        summary = summarizer.summarize(request.text, context_documents=context_docs)
        
        return {
            "summary": summary,
            "context_count": len(context_docs)
        }
    except Exception as e:
        logger.error(f"Summarization failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))
