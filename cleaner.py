import re
from bs4 import BeautifulSoup

class ContentCleaner:
    """
    Cleans raw email content (HTML/Text) for LLM consumption.
    Strips HTML tags, preserves links/structure, and inserts placeholders.
    """

    @staticmethod
    def clean_html(html_content):
        """
        Parses HTML, removes tags, and returns clean text.
        Inserts [IMAGE REMOVED] for <img> tags.
        """
        if not html_content:
            return ""

        soup = BeautifulSoup(html_content, "html.parser")

        # 1. Handle Images: Replace <img> with placeholder
        for img in soup.find_all("img"):
            alt_text = img.get("alt", "")
            placeholder = f" [IMAGE REMOVED: {alt_text}] " if alt_text else " [IMAGE REMOVED] "
            img.replace_with(placeholder)

        # 2. Handle Links: Preserve href in markdown format if possible, or just text
        # For MVP, we'll keep it simple: just text. 
        # Future: Convert <a href="...">text</a> to [text](...)
        
        # 3. Strip Scripts and Styles
        for script in soup(["script", "style"]):
            script.decompose()

        # 4. Get Text
        text = soup.get_text(separator="\n")

        # 5. Collapse Whitespace
        # Replace multiple newlines with max 2, trim lines
        lines = (line.strip() for line in text.splitlines())
        chunks = (phrase.strip() for line in lines for phrase in line.split("  "))
        text = '\n'.join(chunk for chunk in chunks if chunk)
        
        return text

    @staticmethod
    def clean_email_body(email_data):
        """
        Extracts and cleans body from Graph API email object.
        Prioritizes 'body.content' (HTML) > 'bodyPreview'.
        """
        body_content = email_data.get("body", {}).get("content", "")
        content_type = email_data.get("body", {}).get("contentType", "text")
        
        if content_type.lower() == "html":
            return ContentCleaner.clean_html(body_content)
        else:
            # It's already text, but might still need whitespace cleanup
            return body_content.strip()

if __name__ == "__main__":
    # Test Block
    raw_html = """
    <html>
        <body>
            <h1>Weekly Newsletter</h1>
            <p>Here is the latest update.</p>
            <img src="banner.jpg" alt="Chart of Growth">
            <p>Visit our <a href="https://example.com">website</a>.</p>
            <div style="display:none">Hidden Tracking Pixel</div>
        </body>
    </html>
    """
    print("🧹 Cleaning Test:")
    cleaned = ContentCleaner.clean_html(raw_html)
    print("--- RAW ---")
    print(raw_html)
    print("--- CLEAN ---")
    print(cleaned)
