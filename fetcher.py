import os
import json
import logging
import requests
import sys
from pathlib import Path
from config import Config
from auth import authenticate_outlook

# Configure logging
logging.basicConfig(level=getattr(logging, Config.LOG_LEVEL))
logger = logging.getLogger(__name__)

class OutlookFetcher:
    """
    Fetches emails from Outlook via Microsoft Graph API.
    Supports incremental fetching via state persistence.
    """
    
    GRAPH_API_ENDPOINT = "https://graph.microsoft.com/v1.0/me/mailFolders/Inbox/messages"
    
    def __init__(self):
        try:
            self.access_token = authenticate_outlook()
        except Exception as e:
            logger.critical(f"Auth failed in Fetcher: {e}")
            sys.exit(1)
            
        self.headers = {
            "Authorization": f"Bearer {self.access_token}",
            "Content-Type": "application/json"
        }
        self.state = self._load_state()

    def _load_state(self):
        """Loads the last processed email timestamp from state file."""
        if os.path.exists(Config.STATE_FILE):
            try:
                with open(Config.STATE_FILE, "r") as f:
                    return json.load(f)
            except Exception as e:
                logger.warning(f"Failed to load state file: {e}")
        return {"last_received_datetime": None}

    def _save_state(self, last_received_datetime):
        """Saves the latest processed email timestamp."""
        # Only update if the new timestamp is newer
        current_stored = self.state.get("last_received_datetime")
        
        # Simple string comparison works for ISO 8601 dates
        if current_stored and last_received_datetime <= current_stored:
            return

        self.state["last_received_datetime"] = last_received_datetime
        try:
            with open(Config.STATE_FILE, "w") as f:
                json.dump(self.state, f, indent=4)
        except Exception as e:
            logger.error(f"Failed to save state file: {e}")

    def fetch_emails(self, limit=10):
        """
        Fetches the latest emails from the Inbox.
        If state exists, fetches only emails newer than last_received_datetime.
        """
        params = {
            "$top": limit,
            "$select": "id,subject,receivedDateTime,bodyPreview,isRead,from",
            "$orderby": "receivedDateTime desc",
            "$filter": "isDraft eq false" # Exclude drafts
        }

        # Incremental fetching logic
        last_time = self.state.get("last_received_datetime")
        if last_time:
            # Graph API filter for newer emails
            # Using gt (greater than) to avoid re-fetching the exact same second if possible
            # Note: Outlook time precision implies risks of missing emails in same second, 
            # for MVP we assume low volume.
            # FIX: Remove quotes around ISO datetime string for OData DateTimeOffset comparison
            params["$filter"] += f" and receivedDateTime gt {last_time}"
            logger.info(f"Fetching emails received after {last_time}...")
        else:
            logger.info("No previous state found. Fetching latest emails...")

        try:
            response = requests.get(self.GRAPH_API_ENDPOINT, headers=self.headers, params=params)
            response.raise_for_status()
            data = response.json()
            
            messages = data.get("value", [])
            logger.info(f"Fetched {len(messages)} emails.")
            
            if messages:
                # Client-side deduplication:
                # Graph API time precision can be tricky. If the last email has the exact same 
                # timestamp as our state, it might be returned again even with 'gt'.
                # We filter out any message that has a receivedDateTime <= last_time.
                if last_time:
                    messages = [m for m in messages if m["receivedDateTime"] > last_time]
                    logger.info(f"Filtered to {len(messages)} new emails after deduplication.")

                if messages:
                    # Update state with the most recent email's time
                    # Since we ordered by desc, the first item is the newest
                    newest_time = messages[0]["receivedDateTime"]
                    self._save_state(newest_time)
                
            return messages

        except requests.exceptions.HTTPError as e:
            logger.error(f"HTTP Error: {e}")
            if e.response.content:
                 logger.error(f"Response details: {e.response.content.decode()}")
            raise
        except Exception as e:
            logger.error(f"Fetch failed: {e}")
            raise

if __name__ == "__main__":
    # Test Block
    try:
        print("📧 Starting Fetcher Test...")
        fetcher = OutlookFetcher()
        emails = fetcher.fetch_emails(limit=5)
        
        print(f"\n✅ Successfully fetched {len(emails)} emails:")
        for email in emails:
            sender_name = email.get('from', {}).get('emailAddress', {}).get('name', 'Unknown')
            subject = email.get('subject', 'No Subject')
            received = email.get('receivedDateTime')
            print(f"- [{received}] {subject} (From: {sender_name})")
            
    except Exception as e:
        print(f"\n❌ Test failed: {e}")
