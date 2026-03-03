/**
 * API Client for FeedFlux Backend
 * Provides type-safe functions for interacting with FastAPI endpoints
 */

export interface FeedItem {
    id: string;
    subject: string;
    sender: string;
    received_datetime: number;  // Unix timestamp
    body_preview: string;
}

export interface SummaryResponse {
    summary: string;
    context_count: number;
    model: string;  // AI model name
    generated_at: number;  // Unix timestamp
}

/**
 * Fetch recent emails from the backend
 * @param limit - Maximum number of emails to fetch (default: 10)
 * @param q - Optional search keyword
 */
export async function getFeed(limit: number = 10, q?: string): Promise<FeedItem[]> {
    let url = `/api/feed?limit=${limit}`;
    if (q) {
        url += `&q=${encodeURIComponent(q)}`;
    }
    const response = await fetch(url);

    if (!response.ok) {
        throw new Error(`Failed to fetch feed: ${response.statusText}`);
    }

    return response.json();
}

/**
 * Generate AI summary for a specific email using RAG
 * @param emailId - Unique identifier for the email
 * @param text - Email body content
 * @param subject - Email subject line
 */
export async function summarizeEmail(
    emailId: string,
    text: string,
    subject: string
): Promise<SummaryResponse> {
    const response = await fetch('/api/summarize', {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json',
        },
        body: JSON.stringify({
            email_id: emailId,
            text,
            subject,
        }),
    });

    if (!response.ok) {
        throw new Error(`Summarization failed: ${response.statusText}`);
    }

    return response.json();
}

/**
 * RAG Chat Engine Types
 */
export interface SourceItem {
    id: string;
    subject: string;
    snippet: string;
}

export interface ChatResponse {
    answer: string;
    sources: SourceItem[];
}

export interface ChatMessageItem {
    role: 'user' | 'assistant';
    content: string;
}

/**
 * Ask the AI a question about the inbox using RAG
 * @param query - The user's natural language question
 * @param chatHistory - Previous conversation context
 */
export async function askInbox(query: string, chatHistory: ChatMessageItem[] = []): Promise<ChatResponse> {
    const response = await fetch('/api/chat', {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json',
        },
        body: JSON.stringify({ query, chat_history: chatHistory }),
    });

    if (!response.ok) {
        throw new Error(`Failed to get answer: ${response.statusText}`);
    }

    return response.json();
}

/**
 * Full Email Details
 */
export interface EmailDetail extends FeedItem {
    body_content: string;
    body_html: string;
}

/**
 * Fetch full details of a specific email
 * @param id - Unique identifier for the email
 */
export async function getEmailDetail(id: string): Promise<EmailDetail> {
    const response = await fetch(`/api/emails/${id}`);

    if (!response.ok) {
        throw new Error(`Failed to fetch email details: ${response.statusText}`);
    }

    return response.json();
}

/**
 * Manually trigger synchronization with Outlook
 * Fetches new emails, saves them to SQLite, and indexes them in ChromaDB.
 */
export async function syncEmails(): Promise<{ synced: number }> {
    const response = await fetch('/api/sync', {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json',
        },
    });

    if (!response.ok) {
        throw new Error(`Sync failed: ${response.statusText}`);
    }

    return response.json();
}
