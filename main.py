import argparse
import logging
import sys
from pathlib import Path
from datetime import datetime
from config import Config
from fetcher import OutlookFetcher
from cleaner import ContentCleaner
from summarizer import ContentSummarizer

# Configure logging
logging.basicConfig(
    level=getattr(logging, Config.LOG_LEVEL),
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

def main():
    parser = argparse.ArgumentParser(description="FeedFlux: AI-powered Email Summarizer")
    parser.add_argument("--limit", type=int, default=5, help="Number of emails to fetch (default: 5)")
    parser.add_argument("--dry-run", action="store_true", help="Fetch and clean only, skip LLM summarization")
    args = parser.parse_args()

    print(f"🚀 Starting FeedFlux (Limit: {args.limit})...")

    try:
        # 1. Authentication & Fetching
        print("📥 Fetching emails from Outlook...")
        fetcher = OutlookFetcher()
        emails = fetcher.fetch_emails(limit=args.limit)
        
        if not emails:
            print("📭 No new emails found.")
            return

        print(f"✅ Found {len(emails)} new emails.")

        # Initialize processors
        cleaner = ContentCleaner()
        if not args.dry_run:
            summarizer = ContentSummarizer()

        # 2. Processing Pipeline
        report_entries = []
        
        for i, email in enumerate(emails, 1):
            subject = email.get("subject", "No Subject")
            sender = email.get("from", {}).get("emailAddress", {}).get("name", "Unknown Sender")
            print(f"\n[{i}/{len(emails)}] Processing: {subject} (from {sender})")

            # Clean
            cleaned_text = cleaner.clean_email_body(email)
            
            if not cleaned_text:
                logger.warning(f"Skipping empty email: {subject}")
                continue

            summary = "Skipped (Dry Run)"
            if not args.dry_run:
                try:
                    print("   ⏳ Summarizing...")
                    summary = summarizer.summarize(cleaned_text)
                except Exception as e:
                    logger.error(f"Summarization failed for '{subject}': {e}")
                    summary = f"[Error: Summarization failed] {e}"

            # Collect for Report
            entry = f"## 📧 {subject}\n**From:** {sender}\n\n**Summary:**\n{summary}\n"
            report_entries.append(entry)

        # 3. Generate Report
        if report_entries:
            # Ensure output directory exists
            output_dir = Path("digests")
            output_dir.mkdir(exist_ok=True)

            timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
            report_filename = output_dir / f"feedflux_digest_{timestamp}.md"
            
            with open(report_filename, "w", encoding="utf-8") as f:
                f.write(f"# 📰 FeedFlux Daily Digest - {datetime.now().strftime('%Y-%m-%d')}\n\n")
                f.write("---\n".join(report_entries))
            
            print(f"\n🎉 Digest generated: {report_filename}")

    except KeyboardInterrupt:
        print("\n🛑 Operation cancelled by user.")
        sys.exit(0)
    except Exception as e:
        logger.critical(f"Fatal error: {e}", exc_info=True)
        sys.exit(1)

if __name__ == "__main__":
    main()
