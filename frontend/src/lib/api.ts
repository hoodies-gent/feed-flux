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

export interface ChatStreamCallbacks {
    onToken: (text: string) => void;
    onSources: (sources: SourceItem[]) => void;
}

/**
 * Streaming variant of askInbox. Reads newline-delimited JSON events from the
 * backend and invokes callbacks as the answer is generated.
 */
export async function askInboxStream(
    query: string,
    chatHistory: ChatMessageItem[] = [],
    callbacks: ChatStreamCallbacks
): Promise<void> {
    const response = await fetch('/api/chat/stream', {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json',
        },
        body: JSON.stringify({ query, chat_history: chatHistory }),
    });

    if (!response.ok || !response.body) {
        throw new Error(`Failed to get answer: ${response.statusText}`);
    }

    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';

    const handleLine = (line: string) => {
        const trimmed = line.trim();
        if (!trimmed) return;
        const event = JSON.parse(trimmed);
        if (event.type === 'token') {
            callbacks.onToken(event.content);
        } else if (event.type === 'sources') {
            callbacks.onSources(event.sources);
        } else if (event.type === 'error') {
            throw new Error(event.content);
        }
    };

    while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split('\n');
        buffer = lines.pop() || '';
        for (const line of lines) {
            handleLine(line);
        }
    }

    if (buffer) {
        handleLine(buffer);
    }
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

/**
 * Daily Briefing Data
 */
export interface BriefingResponse {
    briefing: string;
    error?: string;
}

/**
 * Fetch the AI-generated daily briefing
 */
export async function getDailyBriefing(): Promise<BriefingResponse> {
    const response = await fetch('/api/briefing');

    if (!response.ok) {
        throw new Error(`Failed to fetch briefing: ${response.statusText}`);
    }

    return response.json();
}

/**
 * Action-Oriented AI: Draft Generation
 */
export interface DraftRequest {
    email_id: string;
    intent: string;
    custom_prompt?: string;
}

export interface DraftResponse {
    draft: string;
}

/**
 * Generate an AI draft reply based on user intention
 */
export async function generateDraftReply(data: DraftRequest): Promise<DraftResponse> {
    const response = await fetch('/api/draft_reply', {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json',
        },
        body: JSON.stringify(data),
    });

    if (!response.ok) {
        throw new Error(`Draft generation failed: ${response.statusText}`);
    }

    return response.json();
}

/**
 * Onboarding & Mock Auth API calls
 */
export async function getConfigStatus(): Promise<{ configured: boolean }> {
    const response = await fetch('/api/config/status');
    if (!response.ok) {
        throw new Error(`Failed to check config status: ${response.statusText}`);
    }
    return response.json();
}

export async function setupConfig(google_api_key: string): Promise<{ success: boolean }> {
    const response = await fetch('/api/config/setup', {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json',
        },
        body: JSON.stringify({ google_api_key }),
    });

    if (!response.ok) {
        throw new Error(`Failed to set up config: ${response.statusText}`);
    }
    return response.json();
}

export async function mockLogin(): Promise<{ success: boolean; token: string }> {
    // TODO: Replace with MSAL @azure/msal-react or proper OAuth redirect when Azure App is ready
    const response = await fetch('/api/auth/mock_login', {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json',
        },
    });

    if (!response.ok) {
        throw new Error(`Mock login failed: ${response.statusText}`);
    }
    return response.json();
}

/**
 * Agent streaming API
 */
export type TraceStep = 'tool_start' | 'tool_end';

export interface TraceEvent {
    step: TraceStep;
    tool?: string;
    args?: unknown;
    output?: string;
}

export interface DraftPreview {
    recipient?: string;
    subject?: string;
    body?: string;
    original_email_id?: string | null;
}

export interface InterruptReference {
    tool: string;
    output: string;
}

export interface BulkTriageItem {
    index: number;
    email_id: string;
    action: 'mark_read' | 'archive';
    reason?: string;
    subject?: string;
    sender?: string;
    sender_email?: string;
    body_preview?: string;
}

export interface NeedsReplyItem {
    email_id: string;
    reason?: string;
    subject?: string;
    sender?: string;
    sender_email?: string;
    body_preview?: string;
}

export interface InterruptEvent {
    tool: string;
    args: Record<string, unknown>;
    tool_call_id: string;
    draft_preview?: DraftPreview;
    references?: InterruptReference[];
    bulk?: BulkTriageItem[];
    needs_reply?: NeedsReplyItem[];
}

export interface BatchDecision {
    index: number;
    approve: boolean;
}

export interface AgentStreamCallbacks {
    onTrace: (t: TraceEvent) => void;
    onToken: (text: string) => void;
    onInterrupt: (i: InterruptEvent) => void;
    onDone: () => void;
    onError?: (msg: string) => void;
}

async function streamAgentNdjson(
    url: string,
    body: object,
    cb: AgentStreamCallbacks
): Promise<void> {
    const res = await fetch(url, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
    });
    if (!res.ok || !res.body) {
        throw new Error(`Agent request failed: ${res.statusText}`);
    }

    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';

    const handleLine = (line: string) => {
        const trimmed = line.trim();
        if (!trimmed) return;
        const ev = JSON.parse(trimmed);
        switch (ev.type) {
            case 'trace':
                cb.onTrace({ step: ev.step, tool: ev.tool, args: ev.args, output: ev.output });
                break;
            case 'token':
                cb.onToken(ev.content);
                break;
            case 'interrupt':
                cb.onInterrupt({
                    tool: ev.tool,
                    args: ev.args,
                    tool_call_id: ev.tool_call_id,
                    draft_preview: ev.draft_preview,
                    references: ev.references,
                    bulk: ev.bulk,
                    needs_reply: ev.needs_reply,
                });
                break;
            case 'done':
                cb.onDone();
                break;
            case 'error':
                cb.onError?.(ev.content);
                break;
        }
    };

    while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split('\n');
        buffer = lines.pop() || '';
        for (const line of lines) handleLine(line);
    }
    if (buffer) handleLine(buffer);
}

export function askAgentStream(
    threadId: string,
    message: string,
    cb: AgentStreamCallbacks
): Promise<void> {
    return streamAgentNdjson('/api/agent/chat/stream', { thread_id: threadId, message }, cb);
}

export function resumeAgent(
    threadId: string,
    approve: boolean,
    note: string | undefined,
    cb: AgentStreamCallbacks,
    editedBody?: string,
    decisions?: BatchDecision[],
): Promise<void> {
    const body: Record<string, unknown> = { thread_id: threadId, approve, note };
    if (editedBody !== undefined) body.edited_body = editedBody;
    if (decisions !== undefined) body.decisions = decisions;
    return streamAgentNdjson('/api/agent/resume', body, cb);
}
