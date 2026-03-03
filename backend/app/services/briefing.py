import os
import json
import logging
from typing import List, Dict
import google.generativeai as genai
from app.core.config import Config
from app.services.database import DatabaseService

logger = logging.getLogger("feedflux-briefing")

class BriefingEngine:
    """
    Synthesizes multiple recent emails into a single executive summary.
    """
    def __init__(self):
        self.db = DatabaseService()
        
        # Configure Gemini
        api_key = Config.GOOGLE_API_KEY
        if api_key:
            genai.configure(api_key=api_key)
            # Use the flash-lite model for quick synthesis
            self.model = genai.GenerativeModel(Config.GEMINI_MODEL_NAME)
        else:
            logger.warning("GOOGLE_API_KEY not found. Briefing engine cannot operate.")
            self.model = None

    def _build_briefing_prompt(self, emails: List[Dict]) -> str:
        """
        Constructs the prompt forcing the AI to synthesize the batch into a briefing.
        """
        prompt = """You are an elite Executive Assistant for the user. Your job is to create a 'Daily Briefing'.
I will give you a list of recent emails from their inbox.
Analyze them together and write a single, cohesive, 3-bullet-point morning briefing.

Formatting rules:
1. Output exactly 3 main bullet points.
2. Use markdown. You can bold important entities or deadlines.
3. Be concise and authoritative. Do not say 'Here is your briefing', just output the bullets.
4. Try to draw connections if two emails are related.
\n\n=== RECENT EMAILS ===\n"""

        for idx, email in enumerate(emails):
            prompt += f"\n[Email {idx+1}]\n"
            prompt += f"Subject: {email.get('subject', 'No Subject')}\n"
            prompt += f"Sender: {email.get('sender_name')} ({email.get('sender_email')})\n"
            # Limit the content size per email so we don't blow up the context window
            content = email.get('body_content', email.get('body_preview', ''))
            prompt += f"Content: {content[:1000]}...\n"

        return prompt

    def generate_daily_briefing(self) -> str:
        """
        Fetches the latest emails and generates an executive summary.
        """
        if not self.model:
            return "AI service is not configured. Please add an API key."

        # Fetch the 10 most recent emails
        recent_emails = self.db.get_emails(limit=10)
        
        if not recent_emails:
            return "Your inbox is currently empty. No briefing to generate."

        prompt = self._build_briefing_prompt(recent_emails)

        try:
            logger.info("Generating Daily Briefing via AI...")
            response = self.model.generate_content(prompt)
            return response.text.strip()
        except Exception as e:
            logger.error(f"Failed to generate briefing: {e}")
            return "I apologize, but I encountered an error while formulating your morning briefing."
