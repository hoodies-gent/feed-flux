'use client';

import { useEffect, useState } from 'react';
import { formatDistanceToNow } from 'date-fns';
import ReactMarkdown from 'react-markdown';
import { Card, CardContent, CardDescription, CardFooter, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { getFeed, summarizeEmail, type FeedItem, type SummaryResponse } from '@/lib/api';
import { toast } from 'sonner';
import { useDebounce } from 'use-debounce';
import { Input } from "@/components/ui/input";
import { Search, Sparkles, X } from 'lucide-react';
import { ScrollArea } from "@/components/ui/scroll-area";

export default function Home() {
  const [searchQuery, setSearchQuery] = useState('');
  const [debouncedQuery] = useDebounce(searchQuery, 500);
  const [feed, setFeed] = useState<FeedItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [summaries, setSummaries] = useState<Record<string, SummaryResponse>>({});
  const [summarizing, setSummarizing] = useState<Record<string, boolean>>({});
  const [expandedId, setExpandedId] = useState<string | null>(null);

  // Chat/RAG Drawer State
  const [isChatOpen, setIsChatOpen] = useState(false);

  const loadFeed = async (query: string = '') => {
    setLoading(true);
    setError(null);
    try {
      const data = await getFeed(20, query);
      setFeed(data);
      if (!query) {
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

  useEffect(() => {
    loadFeed(debouncedQuery);
  }, [debouncedQuery]);

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
                  disabled={loading}
                >
                  {loading ? 'Loading...' : 'Refresh'}
                </Button>
                <Button disabled>Generate Digest</Button>
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
                  if (!searchQuery.trim()) {
                    toast.error('Please enter a question in the search bar first');
                    return;
                  }
                  setIsChatOpen(true);
                }}
              >
                <Sparkles className="w-4 h-4 mr-2" />
                Ask AI
              </Button>
            </div>
          </header>

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
                      <Button
                        variant="ghost"
                        size="sm"
                        className="text-indigo-600 hover:text-indigo-700 hover:bg-indigo-50 ml-auto"
                        onClick={() => handleSummarize(item)}
                        disabled={isSummarizing}
                      >
                        {isSummarizing ? 'Summarizing...' : summary ? (isExpanded ? 'Hide Summary' : 'Show Summary') : 'Summarize with AI'} →
                      </Button>
                    </CardFooter>
                  </Card>
                );
              })
            )}
          </div>
        </main>

        {/* Right column: AI Sidebar (Step 4 Refactored) */}
        {isChatOpen && (
          <aside className="w-[450px] shrink-0 h-[calc(100vh-4rem)] sticky top-8 flex flex-col bg-white dark:bg-slate-950 rounded-xl border border-slate-200 dark:border-slate-800 shadow-sm overflow-hidden">
            {/* Header */}
            <div className="p-5 border-b border-slate-100 dark:border-slate-800 bg-slate-50/50 dark:bg-slate-900/50 flex justify-between items-start">
              <div>
                <div className="flex items-center gap-2 mb-1">
                  <div className="p-1.5 bg-indigo-100 dark:bg-indigo-900/50 rounded-md">
                    <Sparkles className="w-4 h-4 text-indigo-600 dark:text-indigo-400" />
                  </div>
                  <h2 className="text-lg font-semibold text-slate-900 dark:text-slate-100">Ask your Inbox</h2>
                </div>
                <p className="text-sm text-slate-500 truncate max-w-[300px]">"{searchQuery}"</p>
              </div>
              <Button variant="ghost" size="icon" className="h-8 w-8 -mr-2 text-slate-400 hover:text-slate-600 dark:hover:text-slate-300" onClick={() => setIsChatOpen(false)}>
                <X className="w-4 h-4" />
              </Button>
            </div>

            {/* Scrollable Content Area */}
            <ScrollArea className="flex-1 p-5">
              <div className="space-y-6 pb-6 text-slate-500 italic text-sm">
                to be implemented...
              </div>
            </ScrollArea>
          </aside>
        )}
      </div>
    </div>
  );
}
