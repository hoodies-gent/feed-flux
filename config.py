import os
import sys
from pathlib import Path
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

class Config:
    """
    Centralized configuration management.
    Loads from environment variables and defines defaults.
    """
    
    # Project Paths
    BASE_DIR = Path(__file__).resolve().parent
    
    # Microsoft Graph OAuth
    # Register your app at: https://portal.azure.com/#view/Microsoft_AAD_RegisteredApps/ApplicationsListBlade
    # Select "Accounts in any organizational directory (Any Microsoft Entra ID tenant - Multitenant) and personal Microsoft accounts"
    MS_CLIENT_ID = os.getenv("MS_CLIENT_ID")
    AUTHORITY = "https://login.microsoftonline.com/common"
    
    # Scopes required for the application
    SCOPES = ['User.Read', 'Mail.Read']
    
    # Token Cache
    TOKEN_CACHE_FILE = BASE_DIR / "token_cache.bin"

    LLM_PROVIDER = os.getenv("LLM_PROVIDER", "gemini").lower()
    GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")
    OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
    GEMINI_MODEL_NAME = "gemini-flash-latest"

    # App Persistence
    STATE_FILE = BASE_DIR / "state.json"

    # Logging
    LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()

    # Prompts
    DEFAULT_SUMMARIZE_PROMPT = """
    You are an AI assistant for FeedFlux, a tool that aggregates emails into a daily digest.
    
    Please summarize the following email content.
    - Focus on key information, events, and action items.
    - Ignore marketing fluff, boilerplate text, and unsubscribe links.
    - Format the output as a concise paragraph or bullet points.
    - If the content is very short, just return the content itself.
    
    Input Text:
    {text}
    
    Summary:
    """

    @classmethod
    def validate(cls):
        """
        Validates critical configuration.
        """
        if cls.LLM_PROVIDER not in ["gemini", "openai"]:
            print(f"Error: Unsupported LLM_PROVIDER '{cls.LLM_PROVIDER}'. Must be 'gemini' or 'openai'.", file=sys.stderr)
        
        if not cls.MS_CLIENT_ID:
             print("Warning: MS_CLIENT_ID not found in environment variables. Auth will fail.", file=sys.stderr)
        pass

# Simple validation on import (optional, can be moved to main)
Config.validate()
