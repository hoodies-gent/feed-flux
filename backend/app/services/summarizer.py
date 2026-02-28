import os
import logging
import google.generativeai as genai
from app.core.config import Config

# Configure logging
logging.basicConfig(level=getattr(logging, Config.LOG_LEVEL))
logger = logging.getLogger(__name__)

class ContentSummarizer:
    """
    Summarizes email content using Google Gemini API.
    """
    
    def __init__(self):
        # Initialize Gemini using Config
        if not Config.GOOGLE_API_KEY:
            raise ValueError("GOOGLE_API_KEY is missing in configuration.")
        
        genai.configure(api_key=Config.GOOGLE_API_KEY)
        self.model = genai.GenerativeModel(Config.GEMINI_MODEL_NAME)

    def summarize(self, text, context_documents=None):
        """
        Generates a concise summary of the provided text.
        Optional: context_documents (list of str) to provide historical context.
        """
        if not text:
            return "No content to summarize."

        # Use prompt from Config (or future user input)
        # Truncate text to avoid token limits
        truncated_text = text[:8000]

        # Context Layer
        context_block = ""
        if context_documents:
            context_items = "\n".join([f"<item>{doc}</item>" for doc in context_documents])
            context_block = f"<history>\n{context_items}\n</history>\n"
            logger.info(f"Injecting {len(context_documents)} context items into prompt.")

        # Security & Structural Layer
        # 1. Force XML encapsulation for raw content
        safe_content = f"<content>\n{truncated_text}\n</content>"
        
        # 2. System Preamble: Explicitly tell LLM about the structure
        system_preamble = "The content to analyze is enclosed in <content> tags."
        if context_block:
             system_preamble += " Relevant historical context is provided in <history> tags. Use it to identify connections but focus on the new content."
        
        system_preamble += " Please process it according to the instructions below."

        # Base instructions
        instructions = Config.DEFAULT_SUMMARIZE_PROMPT
        
        # Inject custom user instructions if provided
        if getattr(Config, 'CUSTOM_SUMMARY_PROMPT', None):
            logger.info("Injecting custom summary prompt instructions.")
            instructions += f"\n\n**CRITICAL USER INSTRUCTION:**\n{Config.CUSTOM_SUMMARY_PROMPT}\n"
            instructions += "You MUST strictly follow the CRITICAL USER INSTRUCTION above when formatting and reasoning your summary."

        prompt = f"{system_preamble}\n\n{instructions}\n\n{context_block}{safe_content}"
        
        try:
            response = self.model.generate_content(prompt)
            return response.text
        except Exception as e:
            logger.error(f"Summarization failed: {e}")
            return f"[Error: Could not generate summary. Reason: {str(e)}]"

    def answer_question(self, query: str, memory_service) -> dict:
        """
        Answers a user's question using the RAG approach (retrieving context from memory).
        """
        if not query:
            return {"answer": "Ask me anything!", "sources": []}
            
        logger.info(f"Answering query: {query}")
        
        # 1. Retrieve relevant context
        results = memory_service.query_related(query_text=query, n_results=5)
        
        sources = []
        context_block = ""
        
        if results and results.get('documents') and len(results['documents'][0]) > 0:
            docs = results['documents'][0]
            metadatas = results['metadatas'][0]
            ids = results['ids'][0]
            
            context_items = []
            for i, (doc, meta, doc_id) in enumerate(zip(docs, metadatas, ids)):
                subject = meta.get('subject', 'Unknown Subject')
                sender = meta.get('sender', 'Unknown Sender')
                context_items.append(f"<source_email id='{doc_id}' subject='{subject}' sender='{sender}'>\n{doc}\n</source_email>")
                
                sources.append({
                    "id": doc_id,
                    "subject": subject,
                    "snippet": doc[:200] + "..." if len(doc) > 200 else doc
                })
                
            context_block = "\n".join(context_items)
            
        if not context_block:
            return {
                "answer": "I couldn't find any relevant emails in your inbox to answer this question.",
                "sources": []
            }
            
        # 2. Construct Prompt
        system_prompt = (
            "You are an intelligent inbox assistant answering the user's question based strictly on their email history.\n"
            "Below are relevant email snippets from the user's inbox enclosed in <source_email> tags.\n\n"
            f"{context_block}\n\n"
            "INSTRUCTIONS:\n"
            "1. Answer the user's question concisely using ONLY the provided email sources.\n"
            "2. If the answer cannot be confidently determined from the sources, say so explicitly. Do not guess or rely on external knowledge.\n"
            "3. Format your answer using Markdown for readability.\n"
        )
        
        prompt = f"{system_prompt}\n\nUser Question: {query}\n\nAssistant Answer:"
        
        # 3. Generate Answer
        try:
             response = self.model.generate_content(prompt)
             return {
                 "answer": response.text,
                 "sources": sources
             }
        except Exception as e:
             logger.error(f"Failed to generate answer for query '{query}': {e}")
             return {
                 "answer": f"I'm sorry, I encountered an error while trying to answer your question: {str(e)}",
                 "sources": sources
             }

