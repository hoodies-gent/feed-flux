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
