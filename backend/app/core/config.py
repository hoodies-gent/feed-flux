import os
import sys
from pathlib import Path
from dotenv import load_dotenv

# Load environment variables from .env file
CORE_DIR = Path(__file__).resolve().parent
BACKEND_DIR = CORE_DIR.parent.parent
load_dotenv(BACKEND_DIR / ".env")

class Config:
    """
    Centralized configuration management.
    Loads from environment variables and defines defaults.
    """
    
    # Project Paths
    # backend/app/core/config.py -> core -> app -> backend -> feed-flux (ROOT)
    CORE_DIR = Path(__file__).resolve().parent
    APP_DIR = CORE_DIR.parent
    BACKEND_DIR = APP_DIR.parent
    ROOT_DIR = BACKEND_DIR.parent
    
    DATA_DIR = ROOT_DIR / "data"
    
    # Microsoft Graph OAuth
    # Register your app at: https://portal.azure.com/#view/Microsoft_AAD_RegisteredApps/ApplicationsListBlade
    # Select "Accounts in any organizational directory (Any Microsoft Entra ID tenant - Multitenant) and personal Microsoft accounts"
    MS_CLIENT_ID = os.getenv("MS_CLIENT_ID")
    AUTHORITY = "https://login.microsoftonline.com/common"
    
    # Scopes required for the application
    SCOPES = ['User.Read', 'Mail.Read']
    
    # Token Cache
    TOKEN_CACHE_FILE = DATA_DIR / "token_cache.bin"

    LLM_PROVIDER = os.getenv("LLM_PROVIDER", "gemini").lower()
    GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")
    OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

    # AI Feature Flags & Configs
    CUSTOM_SUMMARY_PROMPT = os.getenv("CUSTOM_SUMMARY_PROMPT")  # Optional user-defined instructions for summarization

    # Model Configuration
    # We use 'gemini-2.0-flash-lite' as the code default because it is the standard stable version.
    # However, for environments with strict rate limits (like current Free Tier), we override this 
    # via the GEMINI_MODEL_NAME environment variable to use 'gemini-flash-lite-latest' (or similar).
    # Precedence: Environment Variable > Default Value
    GEMINI_MODEL_NAME = os.getenv("GEMINI_MODEL_NAME", "gemini-2.0-flash-lite")

    # App Persistence
    STATE_FILE = DATA_DIR / "state.json"

    # Logging
    LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()

    # Prompts
    DEFAULT_SUMMARIZE_PROMPT = """
    You are an AI assistant for FeedFlux, a tool that aggregates emails into a daily digest.
    
    Please summarize the content.
    - Focus on key information, events, and action items.
    - Ignore marketing fluff, boilerplate text, and unsubscribe links.
    - Format the output as a concise paragraph or bullet points.
    - If the content is very short, just return the content itself.
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
