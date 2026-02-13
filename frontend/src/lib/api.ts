/**
 * API Client for FeedFlux Backend
 * Provides type-safe functions for interacting with FastAPI endpoints
 */

export interface FeedItem {
    id: string;
    subject: string;
    sender: string;
    received_datetime: string;
    body_preview: string;
}

export interface SummaryResponse {
    summary: string;
    context_count: number;
}

/**
 * Fetch recent emails from the backend
 * @param limit - Maximum number of emails to fetch (default: 10)
 */
export async function getFeed(limit: number = 10): Promise<FeedItem[]> {
    const response = await fetch(`/api/feed?limit=${limit}`);

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
