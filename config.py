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
    
    # Google OAuth
    # The file path to the client secrets (downloaded from Google Cloud)
    CLIENT_SECRETS_FILE = BASE_DIR / os.getenv("GOOGLE_CLIENT_SECRETS_FILE", "credentials.json")
    
    # The file path to store the user's access and refresh tokens
    TOKEN_FILE = BASE_DIR / "token.json"
    
    # Scopes required for the application
    # We need read-only access to messages to fetch them
    SCOPES = ['https://www.googleapis.com/auth/gmail.readonly']

    # LLM Configuration
    LLM_PROVIDER = os.getenv("LLM_PROVIDER", "gemini").lower()
    GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")
    OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

    # App Persistence
    STATE_FILE = BASE_DIR / "state.json"

    # Logging
    LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()

    @classmethod
    def validate(cls):
        """
        Validates critical configuration.
        """
        if cls.LLM_PROVIDER not in ["gemini", "openai"]:
            print(f"Error: Unsupported LLM_PROVIDER '{cls.LLM_PROVIDER}'. Must be 'gemini' or 'openai'.", file=sys.stderr)
            # We don't exit here to allow for partial usage if needed, but it's a critical warning.
        
        # Note: We don't check for API keys here strictly because they might be loaded later or 
        # the user might be running a sub-command that doesn't need them immediately.
        # But for a robust app, we might want to fail early.
        pass

# Simple validation on import (optional, can be moved to main)
Config.validate()
