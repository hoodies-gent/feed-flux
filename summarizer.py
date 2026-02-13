import os
import logging
import google.generativeai as genai
from config import Config

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

    def summarize(self, text):
        """
        Generates a concise summary of the provided text.
        """
        if not text:
            return "No content to summarize."

        # Use prompt from Config
        # Truncate text to avoid token limits (simple char limit for now)
        truncated_text = text[:8000]
        prompt = Config.DEFAULT_SUMMARIZE_PROMPT.format(text=truncated_text)
        
        try:
            response = self.model.generate_content(prompt)
            return response.text
        except Exception as e:
            logger.error(f"Summarization failed: {e}")
            return f"[Error: Could not generate summary. Reason: {str(e)}]"
