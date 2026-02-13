import chromadb
from chromadb import Documents, EmbeddingFunction, Embeddings
import google.generativeai as genai
import logging
from app.core.config import Config

logger = logging.getLogger(__name__)

class GeminiEmbeddingFunction(EmbeddingFunction):
    """
    Custom Embedding Function for ChromaDB using Google Gemini API.
    """
    def __init__(self):
        if not Config.GOOGLE_API_KEY:
            raise ValueError("GOOGLE_API_KEY is not set for Embeddings.")
        genai.configure(api_key=Config.GOOGLE_API_KEY)
        
    def __call__(self, input: Documents) -> Embeddings:
        """
        Computes embeddings for a list of documents.
        """
        model = "models/gemini-embedding-001"
        try:
            # Gemini embedding API expects specific format
            # Using batch embedding if possible, or loop
            # genai.embed_content supports batching
            result = genai.embed_content(
                model=model,
                content=input,
                task_type="retrieval_document",
                title="Email Content" # Optional title
            )
            # The result structure depends on input. If list, 'embedding' is list of lists
            return result['embedding']
        except Exception as e:
            logger.error(f"Gemini Embedding failed: {e}")
            return []

class MemoryService:
    """
    Manages the Vector Database (ChromaDB) for storing and retrieving email context.
    """
    def __init__(self):
        self.persist_dir = str(Config.DATA_DIR / "vector_db")
        self.client = chromadb.PersistentClient(path=self.persist_dir)
        
        # Initialize Gemini Embedding Function
        self.embedding_fn = GeminiEmbeddingFunction()
        
        # Get or Create Collection
        self.collection = self.client.get_or_create_collection(
            name="email_context",
            embedding_function=self.embedding_fn,
            metadata={"description": "Email summaries and content for RAG"}
        )
        logger.info(f"MemoryService initialized. Vector DB at: {self.persist_dir}")

    def add_email(self, email_id: str, text: str, metadata: dict):
        """
        Adds an email to the vector database.
        """
        try:
            # Check if exists to avoid duplicates (naive check)
            existing = self.collection.get(ids=[email_id])
            if existing['ids']:
                logger.debug(f"Email {email_id} already exists in memory using upsert strategy.")
            
            self.collection.upsert(
                documents=[text],
                metadatas=[metadata],
                ids=[email_id]
            )
            logger.info(f"Persisted email {email_id} to memory.")
        except Exception as e:
            logger.error(f"Failed to add email to memory: {e}")

    def query_related(self, query_text: str, n_results: int = 3, filter_metadata: dict = None):
        """
        Retrieves top-N related emails based on semantic similarity.
        """
        try:
            results = self.collection.query(
                query_texts=[query_text],
                n_results=n_results,
                where=filter_metadata # Optional metadata filtering
            )
            return results
        except Exception as e:
            logger.error(f"Query failed: {e}")
            return None
