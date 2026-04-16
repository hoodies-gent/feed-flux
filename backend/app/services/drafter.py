import logging
import google.generativeai as genai
from tenacity import retry, stop_after_attempt, wait_exponential
from langchain_text_splitters import RecursiveCharacterTextSplitter
from app.core.config import Config

logger = logging.getLogger(__name__)

class EmailDrafter:
    """
    Action-Oriented AI: Generates email reply drafts based on context and user intent.
    """
    def __init__(self):
        api_key = Config.GOOGLE_API_KEY
        if api_key:
            genai.configure(api_key=api_key)
            # Use flash-lite or standard fast model
            self.model = genai.GenerativeModel(Config.GEMINI_MODEL_NAME)
        else:
            logger.warning("GOOGLE_API_KEY is not set. Drafter cannot operate.")
            self.model = None

    def _build_draft_prompt(self, email_subject: str, email_content: str, intent: str, custom_prompt: str = None) -> str:
        """
        Constructs the strict persona prompt for creating professional replies.
        """
        # Base Persona
        prompt = """You are an elite, high-EQ Executive Assistant. 
Your task is to draft a professional email reply on behalf of your user.

The draft must strictly follow these rules:
1. Tone must be professional, concise, and match the specified intent exactly.
2. DO NOT include any introductory or concluding meta-commentary (e.g., "Here is your draft:").
3. Output ONLY the raw email body content.
4. Format the output using clean Markdown. Leave placeholders like `[Your Name]` where necessary.
5. If addressing someone, use clues from the original email to infer their name if possible.\n"""

        # Intent Direction
        prompt += f"\n=== CORE INTENT ===\nThe core objective of this reply is: '{intent}'."
        if custom_prompt:
            prompt += f"\nFollow these specific instructions from the user: '{custom_prompt}'"

        # Handle long content with text splitter
        splitter = RecursiveCharacterTextSplitter(chunk_size=4000, chunk_overlap=200)
        chunks = splitter.split_text(email_content)
        truncated_content = chunks[0] if chunks else ""
        if len(chunks) > 1:
            truncated_content += "...\n[Content truncated for length]"

        # Context Payload
        prompt += f"\n\n=== ORIGINAL EMAIL ===\nSubject: {email_subject}\nContent: {truncated_content}"
        
        return prompt

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
    def generate_draft(self, subject: str, content: str, intent: str, custom_prompt: str = None) -> str:
        """
        Synchronous call to Gemini to generate the draft string.
        """
        if not self.model:
            raise ValueError("AI service is not configured. Missing API Key.")
            
        prompt = self._build_draft_prompt(subject, content, intent, custom_prompt)
        
        try:
            logger.info(f"Generating draft for intent: '{intent}'")
            response = self.model.generate_content(prompt)
            return response.text.strip()
        except Exception as e:
            logger.error(f"Draft generation failed: {e}")
            raise RuntimeError(f"Failed to generate draft: {str(e)}")
