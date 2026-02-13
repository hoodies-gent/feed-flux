import os
import atexit
import logging
import msal
from config import Config

# Configure logging
logging.basicConfig(level=getattr(logging, Config.LOG_LEVEL))
logger = logging.getLogger(__name__)

def _load_cache():
    """Loads the token cache from disk if it exists."""
    cache = msal.SerializableTokenCache()
    if os.path.exists(Config.TOKEN_CACHE_FILE):
        try:
            with open(Config.TOKEN_CACHE_FILE, "r") as f:
                cache.deserialize(f.read())
        except Exception as e:
            logger.warning(f"Failed to load token cache: {e}")
    return cache

def _save_cache(cache):
    """Saves the token cache to disk."""
    if cache.has_state_changed:
        try:
            with open(Config.TOKEN_CACHE_FILE, "w") as f:
                f.write(cache.serialize())
        except Exception as e:
            logger.error(f"Failed to save token cache: {e}")

def get_ms_graph_client():
    """
    Initializes the MSAL PublicClientApplication.
    """
    if not Config.MS_CLIENT_ID:
        raise ValueError("MS_CLIENT_ID is not set in environment variables.")

    cache = _load_cache()
    
    # Create the app instance
    app = msal.PublicClientApplication(
        Config.MS_CLIENT_ID,
        authority=Config.AUTHORITY,
        token_cache=cache
    )
    return app

def authenticate_outlook():
    """
    Authenticates the user with Microsoft Graph API via Device Code Flow.
    Returns the access token.
    """
    app = get_ms_graph_client()
    accounts = app.get_accounts()
    
    result = None
    
    # 1. Try to acquire token silently from cache
    if accounts:
        logger.info(f"Found account in cache: {accounts[0]['username']}")
        result = app.acquire_token_silent(Config.SCOPES, account=accounts[0])

    # 2. If silent fails, initiate Device Code Flow
    if not result:
        logger.info("No suitable token in cache. Initiating Device Code Flow...")
        
        flow = app.initiate_device_flow(scopes=Config.SCOPES)
        if "user_code" not in flow:
            raise RuntimeError(f"Failed to create device flow. Response: {flow}")

        print(f"\n👉 To sign in, use a web browser to open the page {flow['verification_uri']} and enter the code {flow['user_code']} to authenticate.\n")
        
        # Block until user logs in
        result = app.acquire_token_by_device_flow(flow)

    # 3. Handle Result
    if "access_token" in result:
        _save_cache(app.token_cache)
        logger.info("Authentication successful. Token cached.")
        return result["access_token"]
    else:
        logger.error(f"Authentication failed: {result.get('error')}")
        logger.error(f"Description: {result.get('error_description')}")
        raise RuntimeError(f"Authentication failed: {result.get('error')}")

if __name__ == "__main__":
    try:
        print("🔐 Starting Outlook Authentication Test...")
        token = authenticate_outlook()
        print("\n✅ Authentication successful!")
        print(f"Token: {token[:15]}...")
    except Exception as e:
        print(f"\n❌ Authentication failed: {e}")
