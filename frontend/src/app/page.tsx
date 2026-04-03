'use client';

import { useEffect, useState, useRef } from 'react';
import { formatDistanceToNow } from 'date-fns';
import ReactMarkdown from 'react-markdown';
import { Card, CardContent, CardDescription, CardFooter, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { getFeed, summarizeEmail, getEmailDetail, syncEmails, askInbox, getDailyBriefing, generateDraftReply, getConfigStatus, setupConfig, mockLogin, type FeedItem, type SummaryResponse, type EmailDetail, type SourceItem, type BriefingResponse, type DraftRequest } from '@/lib/api';
import { toast } from 'sonner';
import { useDebounce } from 'use-debounce';
import { Input } from "@/components/ui/input";
import { Trash2, Send, RefreshCw, X, Sparkles, Search, Copy, Check, ChevronDown, ChevronUp } from 'lucide-react';
import { ScrollArea } from "@/components/ui/scroll-area";
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { ResizableHandle, ResizablePanel, ResizablePanelGroup } from "@/components/ui/resizable";
import { type PanelImperativeHandle } from "react-resizable-panels";
import { cn } from "@/lib/utils";

interface ChatMessage {
  id: string;
  role: 'user' | 'assistant';
  content: string;
  sources?: SourceItem[];
  isLoading?: boolean;
}

export default function Home() {
  const [searchQuery, setSearchQuery] = useState('');
  const [debouncedQuery] = useDebounce(searchQuery, 500);
  const [feed, setFeed] = useState<FeedItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [summaries, setSummaries] = useState<Record<string, SummaryResponse>>({});
  const [summarizing, setSummarizing] = useState<Record<string, boolean>>({});
  const [expandedId, setExpandedId] = useState<string | null>(null);
  const [isSyncing, setIsSyncing] = useState(false);

  // Chat/RAG UI State
  const [isChatOpen, setIsChatOpen] = useState(false);
  const [chatMessages, setChatMessages] = useState<ChatMessage[]>([]);
  const [isChatLoaded, setIsChatLoaded] = useState(false);
  // Daily Briefing State
  const [briefing, setBriefing] = useState<string | null>(null);
  const [isBriefingLoading, setIsBriefingLoading] = useState(true);
  const [briefingError, setBriefingError] = useState<string | null>(null);
  const [chatInput, setChatInput] = useState('');
  const [isSendingChat, setIsSendingChat] = useState(false);
  const messagesEndRef = useRef<HTMLDivElement>(null);

  // Load chat history from LocalStorage strictly on client-side mount
  useEffect(() => {
    const saved = localStorage.getItem('feedflux_chat_history');
    if (saved) {
      try {
        setChatMessages(JSON.parse(saved));
      } catch (e) {
        console.error('Failed to parse persistent chat history', e);
      }
    }
    setIsChatLoaded(true);
  }, []);

  // Save chat history to LocalStorage whenever it changes
  useEffect(() => {
    if (isChatLoaded) {
      localStorage.setItem('feedflux_chat_history', JSON.stringify(chatMessages));
    }
  }, [chatMessages, isChatLoaded]);

  // Auto-scroll chat to bottom
  useEffect(() => {
    if (isChatOpen) {
      messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
    }
  }, [chatMessages, isChatOpen]);

  // Email Detail Modal State
  const [selectedEmailId, setSelectedEmailId] = useState<string | null>(null);
  const [emailDetailData, setEmailDetailData] = useState<EmailDetail | null>(null);
  const [isEmailDetailOpen, setIsEmailDetailOpen] = useState(false);
  const [isLoadingDetail, setIsLoadingDetail] = useState(false);

  // Email Drafting State
  const [isDrafting, setIsDrafting] = useState(false);
  const [generatedDraft, setGeneratedDraft] = useState<string | null>(null);
  const [customDraftPrompt, setCustomDraftPrompt] = useState('');
  const [draftCopied, setDraftCopied] = useState(false);

  const handleSendChatMessage = async (e?: React.FormEvent) => {
    e?.preventDefault();
    if (!chatInput.trim() || isSendingChat) return;

    const query = chatInput.trim();
    setChatInput('');
    setIsSendingChat(true);

    const newUserMsg: ChatMessage = { id: Date.now().toString(), role: 'user', content: query };
    const loadingAiMsg: ChatMessage = { id: (Date.now() + 1).toString(), role: 'assistant', content: '', isLoading: true };

    // Map conversation context for the backend
    const historyToSend = chatMessages.map(m => ({
      role: m.role,
      content: m.content
    }));

    setChatMessages(prev => [...prev, newUserMsg, loadingAiMsg]);

    try {
      const result = await askInbox(query, historyToSend);
      setChatMessages(prev =>
        prev.map(msg =>
          msg.id === loadingAiMsg.id
            ? { ...msg, content: result.answer, sources: result.sources, isLoading: false }
            : msg
        )
      );
    } catch (err) {
      toast.error('Failed to get answer from AI');
      setChatMessages(prev =>
        prev.map(msg =>
          msg.id === loadingAiMsg.id
            ? { ...msg, content: 'Sorry, I encountered an error searching your inbox.', isLoading: false }
            : msg
        )
      );
    } finally {
      setIsSendingChat(false);
    }
  };

  const handleOpenEmailDetail = async (id: string) => {
    setSelectedEmailId(id);
    setGeneratedDraft(null);
    setCustomDraftPrompt('');
    setIsEmailDetailOpen(true);
    setIsLoadingDetail(true);
    setEmailDetailData(null);
    try {
      const data = await getEmailDetail(id);
      setEmailDetailData(data);
    } catch (err) {
      toast.error('Failed to load full email content');
      setIsEmailDetailOpen(false);
    } finally {
      setIsLoadingDetail(false);
    }
  };

  const handleGenerateDraft = async (intent: string) => {
    if (!selectedEmailId) return;
    setIsDrafting(true);
    setGeneratedDraft(null);
    setDraftCopied(false);
    try {
      const res = await generateDraftReply({
        email_id: selectedEmailId,
        intent,
        custom_prompt: customDraftPrompt.trim() ? customDraftPrompt.trim() : undefined
      });
      setGeneratedDraft(res.draft);
      toast.success("Draft generated!");
    } catch (err) {
      toast.error("Failed to generate draft.");
    } finally {
      setIsDrafting(false);
    }
  };

  const copyDraftToClipboard = () => {
    if (generatedDraft) {
      navigator.clipboard.writeText(generatedDraft);
      setDraftCopied(true);
      toast.success("Copied to clipboard!");
      setTimeout(() => setDraftCopied(false), 2000);
    }
  };

  const loadFeed = async (query: string = '', silent: boolean = false) => {
    setLoading(true);
    setError(null);
    try {
      const data = await getFeed(20, query);
      setFeed(data);
      if (!query && !silent) {
        toast.success(`Loaded ${data.length} emails`);
      }
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Failed to load feed';
      setError(message);
      toast.error(message);
    } finally {
      setLoading(false);
    }
  };

  const handleSync = async () => {
    setIsSyncing(true);
    try {
      const result = await syncEmails();
      toast.success(`Successfully synced ${result.synced} new emails from Outlook`);
      // Reload feed silently to show new emails
      await loadFeed(debouncedQuery, true);
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Failed to sync emails';
      toast.error(message);
    } finally {
      setIsSyncing(false);
    }
  };

  const [appState, setAppState] = useState<'checking' | 'setup_keys' | 'mock_login' | 'feed'>('checking');
  const [apiKeyInput, setApiKeyInput] = useState('');
  const [isSettingUp, setIsSettingUp] = useState(false);
  const [isMockLoggingIn, setIsMockLoggingIn] = useState(false);

  useEffect(() => {
    const initCheck = async () => {
        try {
            const hasMockLogged = localStorage.getItem('feedflux_mock_logged_in');
            const configRes = await getConfigStatus();
            if (!configRes.configured) {
                setAppState('setup_keys');
            } else if (!hasMockLogged) {
                setAppState('mock_login');
            } else {
                setAppState('feed');
            }
        } catch(e) {
             setAppState('feed');
        }
    };
    initCheck();
  }, []);

  useEffect(() => {
    if (appState === 'feed') {
      loadFeed(debouncedQuery);
    }
  }, [debouncedQuery, appState]);

  const handleSetupKeys = async () => {
      if (!apiKeyInput.trim()) return;
      setIsSettingUp(true);
      try {
          await setupConfig(apiKeyInput.trim());
          toast.success("API Key saved securely.");
          setAppState('mock_login');
      } catch(e) {
          toast.error("Failed to save API key.");
      } finally {
          setIsSettingUp(false);
      }
  };

  const handleMockLogin = async () => {
      setIsMockLoggingIn(true);
      try {
          await mockLogin();
          localStorage.setItem('feedflux_mock_logged_in', 'true');
          toast.success("Successfully authenticated with Outlook!");
          setAppState('feed');
      } catch(e) {
          toast.error("Login failed.");
      } finally {
          setIsMockLoggingIn(false);
      }
  };

  // Load briefing on mount if in feed
  useEffect(() => {
    const fetchBriefing = async () => {
      try {
        setIsBriefingLoading(true);
        setBriefingError(null);
        const res = await getDailyBriefing();
        if (res.error) {
          setBriefingError(res.error);
        } else {
          setBriefing(res.briefing);
        }
      } catch (e) {
        console.error("Failed to load briefing", e);
        setBriefingError("Failed to connect to the intelligence server.");
      } finally {
        setIsBriefingLoading(false);
      }
    };
    fetchBriefing();
  }, []);

  /**
   * Format timestamp to human-readable relative time
   * Example: "2 hours ago", "yesterday"
   */
  const formatTime = (timestamp: number) => {
    try {
      // Convert Unix timestamp (seconds) to milliseconds
      return formatDistanceToNow(new Date(timestamp * 1000), { addSuffix: true });
    } catch {
      return new Date(timestamp * 1000).toLocaleDateString();
    }
  };

  /**
   * Format timestamp to precise datetime string
   * Example: "2026-02-15 17:14:23"
   */
  const formatDateTime = (timestamp: number) => {
    try {
      const date = new Date(timestamp * 1000);
      return date.toLocaleString('en-CA', {
        year: 'numeric',
        month: '2-digit',
        day: '2-digit',
        hour: '2-digit',
        minute: '2-digit',
        second: '2-digit',
        hour12: false
      }).replace(',', '');
    } catch {
      return new Date(timestamp * 1000).toLocaleString();
    }
  };

  /**
   * Generate AI summary for a specific email
   */
  const handleSummarize = async (item: FeedItem) => {
    // If already summarized, just toggle expansion
    if (summaries[item.id]) {
      setExpandedId(expandedId === item.id ? null : item.id);
      return;
    }

    // Start summarization
    setSummarizing(prev => ({ ...prev, [item.id]: true }));
    setExpandedId(item.id);

    try {
      const summary = await summarizeEmail(item.id, item.body_preview, item.subject);
      setSummaries(prev => ({ ...prev, [item.id]: summary }));
      toast.success('Summary generated');
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Summarization failed';
      toast.error(message);
      setExpandedId(null);
    } finally {
      setSummarizing(prev => ({ ...prev, [item.id]: false }));
    }
  };

  if (appState === 'checking') {
    return (
        <div className="min-h-screen bg-slate-50 dark:bg-slate-900 flex items-center justify-center flex-col gap-6">
            <div className="relative">
                <div className="absolute inset-0 bg-indigo-500 blur-xl opacity-20 animate-pulse rounded-full"></div>
                <Sparkles className="w-16 h-16 text-indigo-500 animate-pulse relative z-10" />
            </div>
            <p className="text-slate-500 dark:text-slate-400 font-medium tracking-widest uppercase text-sm">Initializing FeedFlux Core</p>
        </div>
    );
  }

  if (appState !== 'feed') {
    return (
      <div className="min-h-screen flex items-center justify-center">
         <p className="text-slate-500 font-mono">Loading MVP State: [{appState}]...</p>
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-slate-50 dark:bg-slate-900 p-8 font-[family-name:var(--font-geist-sans)]">
      <div className={`mx-auto flex gap-8 items-start transition-all duration-300 ${isChatOpen ? 'max-w-[1400px]' : 'max-w-4xl'}`}>

        {/* Left column: Feed */}
        <main className="flex-1 min-w-0 space-y-8">

          {/* Header & Omnibar */}
          <header className="flex flex-col gap-6 mb-8">
            <div className="flex justify-between items-center">
              <div>
                <h1 className="text-3xl font-bold tracking-tight text-slate-900 dark:text-slate-100">FeedFlux</h1>
                <p className="text-slate-500 dark:text-slate-400 mt-1">Your Intelligent Email Digest</p>
              </div>
              <div className="flex gap-2">
                <Button
                  variant="outline"
                  onClick={() => loadFeed(debouncedQuery)}
                  disabled={loading || isSyncing}
                >
                  {loading ? 'Loading...' : 'Reload Local Data'}
                </Button>
                <Button
                  onClick={handleSync}
                  disabled={isSyncing}
                  className="bg-indigo-600 hover:bg-indigo-700 text-white"
                >
                  <RefreshCw className={`w-4 h-4 mr-2 ${isSyncing ? 'animate-spin' : ''}`} />
                  {isSyncing ? 'Syncing...' : 'Sync from Outlook'}
                </Button>
              </div>
            </div>

            {/* Omnibar (Search & Eventually Ask AI) */}
            <div className="flex gap-3 w-full">
              <div className="relative w-full shadow-sm rounded-md">
                <div className="absolute inset-y-0 left-0 pl-3 flex items-center pointer-events-none">
                  <Search className="h-5 w-5 text-slate-400" />
                </div>
                <Input
                  type="text"
                  placeholder="Search by keyword or ask anything to your inbox (e.g. 'What was the Q1 roadmap?')"
                  className="pl-10 pr-4 py-6 w-full text-base bg-white dark:bg-slate-950 border-slate-200 focus-visible:ring-indigo-500"
                  value={searchQuery}
                  onChange={(e) => setSearchQuery(e.target.value)}
                />
              </div>
              {/* Ask AI Trigger - Minimalist Outline Variant */}
              <Button
                variant="outline"
                className="h-auto py-0 px-6 text-indigo-600 hover:text-indigo-700 hover:bg-indigo-50 border-indigo-200 hover:border-indigo-300 dark:text-indigo-400 dark:border-indigo-800 dark:hover:bg-indigo-900/30 transition-all font-medium whitespace-nowrap"
                onClick={() => {
                  setIsChatOpen(true);
                  if (searchQuery.trim()) {
                    setChatInput(searchQuery);
                    setSearchQuery('');
                  }
                }}
              >
                <Sparkles className="w-4 h-4 mr-2" />
                Ask AI
              </Button>
            </div>
          </header>

          {/* Daily Briefing Banner */}
          {!debouncedQuery && (
            <div className="mb-8 p-6 rounded-2xl bg-gradient-to-br from-indigo-500 via-purple-500 to-pink-500 shadow-lg text-white">
              <div className="flex items-center gap-3 mb-4">
                <Sparkles className="h-6 w-6 text-yellow-300" />
                <h2 className="text-xl font-bold tracking-tight">Morning Intelligence Briefing</h2>
              </div>

              {isBriefingLoading ? (
                <div className="space-y-3">
                  <Skeleton className="h-4 w-3/4 bg-white/20" />
                  <Skeleton className="h-4 w-full bg-white/20" />
                  <Skeleton className="h-4 w-5/6 bg-white/20" />
                </div>
              ) : briefingError ? (
                <div className="bg-black/10 p-4 rounded-lg flex items-start gap-3">
                  <div className="text-white/90 text-sm">{briefingError}</div>
                </div>
              ) : briefing ? (
                <div className="prose prose-sm prose-invert max-w-none">
                  <ReactMarkdown>{briefing}</ReactMarkdown>
                </div>
              ) : (
                <p className="text-white/80">No briefing available today.</p>
              )}
            </div>
          )}

          {/* Feed List */}
          <div className="space-y-4">
            {loading ? (
              // Loading Skeletons
              Array.from({ length: 3 }).map((_, i) => (
                <Card key={i} className="w-full">
                  <CardHeader>
                    <Skeleton className="h-6 w-2/3 mb-2" />
                    <Skeleton className="h-4 w-1/3" />
                  </CardHeader>
                  <CardContent>
                    <Skeleton className="h-4 w-full mb-2" />
                    <Skeleton className="h-4 w-4/5" />
                  </CardContent>
                </Card>
              ))
            ) : error ? (
              // Error State
              <Card className="border-red-200 bg-red-50 dark:bg-red-950/20">
                <CardHeader>
                  <CardTitle className="text-red-700 dark:text-red-400">Failed to Load Feed</CardTitle>
                  <CardDescription>{error}</CardDescription>
                </CardHeader>
                <CardFooter>
                  <Button onClick={() => loadFeed(debouncedQuery)} variant="outline">Try Again</Button>
                </CardFooter>
              </Card>
            ) : feed.length === 0 ? (
              // Empty State
              <Card className="border-dashed">
                <CardHeader>
                  <CardTitle className="text-slate-500">No Emails Found</CardTitle>
                  <CardDescription>Your inbox is empty or all emails have been processed.</CardDescription>
                </CardHeader>
              </Card>
            ) : (
              // Email Cards
              feed.map((item) => {
                const isSummarizing = summarizing[item.id];
                const summary = summaries[item.id];
                const isExpanded = expandedId === item.id;

                return (
                  <Card
                    key={item.id}
                    className="hover:shadow-md transition-shadow border-l-4 border-l-transparent hover:border-l-indigo-500"
                  >
                    <CardHeader>
                      <div className="flex justify-between items-start gap-4">
                        <CardTitle className="text-xl font-semibold text-slate-800 dark:text-slate-100 flex-1">
                          {item.subject}
                        </CardTitle>
                        <Badge variant="secondary" className="text-xs whitespace-nowrap">
                          {formatTime(item.received_datetime)}
                        </Badge>
                      </div>
                      <CardDescription className="text-slate-500">{item.sender}</CardDescription>
                    </CardHeader>
                    <CardContent className="space-y-4">
                      <p className="text-sm text-slate-600 dark:text-slate-300 line-clamp-3">
                        {item.body_preview}
                      </p>

                      {/* AI Summary Section */}
                      {isExpanded && (
                        <div className="border-t pt-4 mt-4">
                          {isSummarizing ? (
                            <div className="space-y-2">
                              <Skeleton className="h-4 w-full" />
                              <Skeleton className="h-4 w-5/6" />
                              <Skeleton className="h-4 w-4/6" />
                            </div>
                          ) : summary ? (
                            <div className="space-y-3">
                              {/* Generation Metadata: AI + Model (left) and Time (right) */}
                              <div className="flex justify-between items-center gap-2 flex-wrap">
                                <div className="flex items-center gap-2">
                                  <Badge variant="outline" className="text-xs">
                                    AI Summary
                                  </Badge>
                                  {summary.model && (
                                    <span className="text-xs text-slate-500">
                                      by {summary.model}
                                    </span>
                                  )}
                                </div>
                                {summary.generated_at && (
                                  <span className="text-xs text-slate-500">
                                    Last generated: {formatDateTime(summary.generated_at)}
                                  </span>
                                )}
                              </div>
                              <div className="prose prose-sm dark:prose-invert max-w-none">
                                <ReactMarkdown>{summary.summary}</ReactMarkdown>
                              </div>
                            </div>
                          ) : null}
                        </div>
                      )}
                    </CardContent>
                    <CardFooter className="flex justify-between items-center pt-0">
                      {/* Related emails count (left side) */}
                      {summary && summary.context_count > 0 && (
                        <span className="text-xs text-slate-500">
                          Found {summary.context_count} related email{summary.context_count > 1 ? 's' : ''}
                        </span>
                      )}
                      <div className="flex gap-2 ml-auto">
                        <Button
                          variant="ghost"
                          size="sm"
                          className="text-slate-600 hover:text-slate-900 hover:bg-slate-100"
                          onClick={() => handleOpenEmailDetail(item.id)}
                        >
                          Read Original ↗
                        </Button>
                        <Button
                          variant="ghost"
                          size="sm"
                          className="text-indigo-600 hover:text-indigo-700 hover:bg-indigo-50"
                          onClick={() => handleSummarize(item)}
                          disabled={isSummarizing}
                        >
                          {isSummarizing ? 'Summarizing...' : summary ? (isExpanded ? 'Hide Summary' : 'Show Summary') : 'Summarize with AI'} →
                        </Button>
                      </div>
                    </CardFooter>
                  </Card>
                );
              })
            )}
          </div>
        </main>

        {/* Right column: AI Sidebar (Multi-Turn Chat) */}
        {isChatOpen && (
          <aside className="w-[450px] shrink-0 h-[calc(100vh-4rem)] sticky top-8 flex flex-col bg-white dark:bg-slate-950 rounded-xl border border-slate-200 dark:border-slate-800 shadow-sm overflow-hidden">
            {/* Header */}
            <div className="p-4 border-b border-slate-100 dark:border-slate-800 bg-slate-50/50 dark:bg-slate-900/50 flex justify-between items-center shrink-0">
              <div className="flex items-center gap-2">
                <div className="p-1.5 bg-indigo-100 dark:bg-indigo-900/50 rounded-md">
                  <Sparkles className="w-4 h-4 text-indigo-600 dark:text-indigo-400" />
                </div>
                <h2 className="text-sm font-semibold text-slate-900 dark:text-slate-100">Inbox QA Assistant</h2>
              </div>
              <div className="flex items-center gap-1">
                {chatMessages.length > 0 && (
                  <Button variant="ghost" size="icon" className="h-8 w-8 text-slate-400 hover:text-red-600 hover:bg-red-50 dark:hover:text-red-400 dark:hover:bg-red-900/30 transition-colors" onClick={() => setChatMessages([])} title="Clear Chat History">
                    <Trash2 className="w-4 h-4" />
                  </Button>
                )}
                <Button variant="ghost" size="icon" className="h-8 w-8 -mr-2 text-slate-400 hover:text-slate-600 dark:hover:text-slate-300" onClick={() => setIsChatOpen(false)}>
                  <X className="w-4 h-4" />
                </Button>
              </div>
            </div>

            {/* Scrollable Content Area */}
            <div className="flex-1 overflow-y-auto p-5 relative">
              <div className="space-y-6 pb-2">
                {chatMessages.length === 0 ? (
                  <div className="h-full flex flex-col items-center justify-center text-center space-y-4 pt-20">
                    <div className="p-4 bg-indigo-50 dark:bg-indigo-900/20 rounded-full">
                      <Sparkles className="w-8 h-8 text-indigo-400" />
                    </div>
                    <div>
                      <h3 className="text-sm font-medium text-slate-900 dark:text-slate-100 mb-1">How can I help you today?</h3>
                      <p className="text-sm text-slate-500 max-w-[250px] mx-auto">Ask me to find specific emails, summarize threads, or extract information from your inbox.</p>
                    </div>
                  </div>
                ) : (
                  chatMessages.map(msg => (
                    <div key={msg.id} className={`flex flex-col ${msg.role === 'user' ? 'items-end' : 'items-start'} gap-1.5`}>
                      <span className="text-[11px] font-medium text-slate-400 px-1">{msg.role === 'user' ? 'You' : 'AI Assistant'}</span>
                      <div className={`px-4 py-3 max-w-[90%] text-sm ${msg.role === 'user' ? 'bg-indigo-600 text-white rounded-2xl rounded-tr-sm' : 'bg-slate-100 dark:bg-slate-800 text-slate-800 dark:text-slate-200 rounded-2xl rounded-tl-sm'}`}>
                        {msg.isLoading ? (
                          <div className="flex gap-1 py-1">
                            <span className="h-1.5 w-1.5 rounded-full bg-slate-400 animate-bounce" style={{ animationDelay: '0ms' }} />
                            <span className="h-1.5 w-1.5 rounded-full bg-slate-400 animate-bounce" style={{ animationDelay: '150ms' }} />
                            <span className="h-1.5 w-1.5 rounded-full bg-slate-400 animate-bounce" style={{ animationDelay: '300ms' }} />
                          </div>
                        ) : (
                          <div className="prose prose-sm dark:prose-invert prose-p:leading-snug max-w-none">
                            <ReactMarkdown>{msg.content}</ReactMarkdown>
                          </div>
                        )}
                      </div>

                      {/* Citations/Sources Cards attached to AI Response */}
                      {msg.role === 'assistant' && msg.sources && msg.sources.length > 0 && (
                        <div className="mt-2 flex flex-wrap gap-1.5 w-[90%]">
                          {msg.sources.map((source, i) => (
                            <button
                              key={i}
                              onClick={() => handleOpenEmailDetail(source.id)}
                              className="flex items-center gap-1.5 px-2.5 py-1 text-[11px] font-medium bg-white dark:bg-slate-800 border border-slate-200 dark:border-slate-700 rounded-full text-slate-600 dark:text-slate-300 hover:bg-slate-50 dark:hover:bg-slate-700 hover:border-indigo-300 transition-colors shadow-sm max-w-full text-left"
                              title={source.snippet}
                            >
                              <span className="text-indigo-500 font-semibold whitespace-nowrap">Source {i + 1}</span>
                              <span className="truncate max-w-[150px]">{source.subject}</span>
                            </button>
                          ))}
                        </div>
                      )}
                    </div>
                  ))
                )}
                <div ref={messagesEndRef} />
              </div>
            </div>

            {/* Input Overlay / Footer */}
            <div className="p-4 bg-white dark:bg-slate-950 border-t border-slate-100 dark:border-slate-800 shrink-0">
              <form onSubmit={handleSendChatMessage} className="relative flex items-center">
                <Input
                  value={chatInput}
                  onChange={(e) => setChatInput(e.target.value)}
                  disabled={isSendingChat}
                  placeholder="Ask a follow-up question..."
                  className="w-full pr-12 rounded-full border-slate-300 dark:border-slate-700 focus-visible:ring-indigo-500 shadow-sm"
                />
                <Button
                  type="submit"
                  disabled={!chatInput.trim() || isSendingChat}
                  size="icon"
                  variant="ghost"
                  className="absolute right-1 text-indigo-600 hover:text-indigo-700 hover:bg-indigo-50 dark:text-indigo-400 dark:hover:bg-indigo-900/30 h-8 w-8 rounded-full"
                >
                  <Send className="h-4 w-4" />
                </Button>
              </form>
            </div>
          </aside>
        )}
      </div>

      {/* View Original Email Modal */}
      <Dialog open={isEmailDetailOpen} onOpenChange={setIsEmailDetailOpen}>
        <DialogContent
          className="max-w-4xl h-[90vh] flex flex-col p-0 overflow-hidden bg-white dark:bg-slate-950"
          onInteractOutside={(e) => {
            // Prevent dragging the resize handle outside the modal from closing it
            e.preventDefault();
          }}
        >
          <DialogHeader className="p-6 border-b border-slate-100 dark:border-slate-800 bg-slate-50/50 dark:bg-slate-900/50 shrink-0">
            <DialogTitle className="text-xl font-semibold text-slate-900 dark:text-slate-100 pr-8">
              {emailDetailData?.subject || "Loading..."}
            </DialogTitle>
            {emailDetailData && (
              <div className="text-sm text-slate-500 mt-2 flex items-center justify-between">
                <span>From: <span className="font-medium text-slate-700 dark:text-slate-300">{emailDetailData.sender}</span></span>
                <span>{formatDateTime(emailDetailData.received_datetime)}</span>
              </div>
            )}
          </DialogHeader>
          {/* Resizable Container wrapping Body & Action Panel */}
          <div className="flex-1 w-full bg-slate-200 dark:bg-slate-800 overflow-hidden relative">
            <ResizablePanelGroup id="email-dialog-group" orientation="vertical">

              {/* TOP PANEL: Original Email */}
              <ResizablePanel id="email-body-panel" defaultSize={70} minSize={25} className="bg-white dark:bg-slate-950 flex flex-col relative pb-4">
                <div className="flex-1 overflow-y-auto w-full p-6">
                  {isLoadingDetail ? (
                    <div className="space-y-4">
                      <Skeleton className="h-4 w-full" />
                      <Skeleton className="h-4 w-[95%]" />
                      <Skeleton className="h-4 w-[90%]" />
                      <Skeleton className="h-4 w-full" />
                      <Skeleton className="h-4 w-[85%]" />
                      <Skeleton className="h-4 w-[90%]" />
                    </div>
                  ) : emailDetailData ? (
                    <div className="prose prose-sm md:prose-base dark:prose-invert max-w-none text-slate-800 dark:text-slate-200">
                      {emailDetailData.body_html ? (
                        <div dangerouslySetInnerHTML={{ __html: emailDetailData.body_html }} />
                      ) : (
                        <div className="whitespace-pre-wrap">{emailDetailData.body_content}</div>
                      )}
                    </div>
                  ) : (
                    <div className="text-center text-red-500">Failed to load email content.</div>
                  )}
                </div>
              </ResizablePanel>

              {/* DRAGGABLE DIVIDER */}
              <ResizableHandle id="email-divider" withHandle className="hover:bg-indigo-300 dark:hover:bg-indigo-700 hover:h-1.5 transition-all outline-none relative group/handle" />

              {/* BOTTOM PANEL: AI Action Panel (Draft Reply) */}
              <ResizablePanel
                id="email-action-panel"
                defaultSize={30}
                minSize={20}
                className="bg-slate-50 dark:bg-slate-900 flex flex-col relative border-t border-slate-200 dark:border-slate-800"
              >
                <div className="p-6 h-full flex flex-col overflow-y-auto">
                  {emailDetailData && !isLoadingDetail ? (
                    <>
                      <div className="flex items-center gap-2 mb-3 shrink-0">
                        <Sparkles className="w-5 h-5 text-indigo-500" />
                        <h3 className="font-semibold text-slate-900 dark:text-slate-100">Draft AI Reply</h3>
                      </div>

                      {/* Intent Buttons */}
                      <div className="flex flex-wrap gap-2 shrink-0 mb-2">
                        <Button variant="outline" size="sm" onClick={() => handleGenerateDraft("Sounds good / Agree / Acknowledge")} disabled={isDrafting}>
                          👍 Sounds good
                        </Button>
                        <Button variant="outline" size="sm" onClick={() => handleGenerateDraft("Polite Decline / Cannot attend")} disabled={isDrafting}>
                          ✋ Polite Decline
                        </Button>
                        <Button variant="outline" size="sm" onClick={() => handleGenerateDraft("Need more info / Ask for details")} disabled={isDrafting}>
                          ❓ Need info
                        </Button>
                        <div className="flex-1 min-w-[200px] flex gap-2">
                          <Input
                            placeholder="Or type custom instructions..."
                            value={customDraftPrompt}
                            onChange={e => setCustomDraftPrompt(e.target.value)}
                            onKeyDown={e => e.key === 'Enter' && handleGenerateDraft("Follow custom instructions")}
                            className="h-9 bg-white dark:bg-slate-950"
                            disabled={isDrafting}
                          />
                          <Button size="sm" onClick={() => handleGenerateDraft("Follow custom instructions")} disabled={isDrafting}>
                            Draft
                          </Button>
                        </div>
                      </div>

                      {/* Loading State or Result */}
                      {isDrafting && (
                        <div className="mt-2 space-y-2 p-4 border border-slate-200 dark:border-slate-800 rounded-lg shrink-0">
                          <Skeleton className="h-4 w-1/4 mb-4" />
                          <Skeleton className="h-4 w-full" />
                          <Skeleton className="h-4 w-5/6" />
                          <Skeleton className="h-4 w-4/6" />
                        </div>
                      )}

                      {generatedDraft && !isDrafting && (
                        <div className="mt-2 p-4 bg-white dark:bg-slate-950 border border-indigo-200 dark:border-indigo-800 rounded-lg relative group flex-1 overflow-y-auto shadow-[inset_0_2px_10px_rgba(0,0,0,0.05)]">
                          <Button
                            size="sm"
                            variant="secondary"
                            className="absolute top-2 right-2 opacity-0 group-hover:opacity-100 transition-opacity bg-slate-100 hover:bg-slate-200 dark:bg-slate-800 dark:hover:bg-slate-700"
                            onClick={copyDraftToClipboard}
                          >
                            {draftCopied ? <Check className="w-4 h-4 mr-2 text-green-600 dark:text-green-400" /> : <Copy className="w-4 h-4 mr-2" />}
                            {draftCopied ? 'Copied' : 'Copy'}
                          </Button>
                          <div className="prose prose-sm dark:prose-invert max-w-none mr-20 whitespace-pre-wrap font-sans text-slate-700 dark:text-slate-300">
                            <ReactMarkdown>{generatedDraft}</ReactMarkdown>
                          </div>
                        </div>
                      )}
                    </>
                  ) : (
                    <div className="flex-1 w-full h-full flex flex-col items-center justify-center text-slate-400 dark:text-slate-600 space-y-3">
                      <Sparkles className="w-8 h-8 opacity-20 animate-pulse" />
                      <p className="text-sm font-medium">Preparing AI Assistant...</p>
                    </div>
                  )}
                </div>
              </ResizablePanel>
            </ResizablePanelGroup>
          </div>
        </DialogContent>
      </Dialog>
    </div >
  );
}
