'use client';

import { useEffect, useState } from 'react';
import { formatDistanceToNow } from 'date-fns';
import { Card, CardContent, CardDescription, CardFooter, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { getFeed, type FeedItem } from '@/lib/api';
import { toast } from 'sonner';

export default function Home() {
  const [feed, setFeed] = useState<FeedItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const loadFeed = async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await getFeed(10);
      setFeed(data);
      toast.success(`Loaded ${data.length} emails`);
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Failed to load feed';
      setError(message);
      toast.error(message);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadFeed();
  }, []);

  /**
   * Format timestamp to human-readable relative time
   * Example: "2 hours ago", "yesterday"
   */
  const formatTime = (datetime: string) => {
    try {
      return formatDistanceToNow(new Date(datetime), { addSuffix: true });
    } catch {
      return new Date(datetime).toLocaleDateString();
    }
  };

  return (
    <div className="min-h-screen bg-slate-50 dark:bg-slate-900 p-8 font-[family-name:var(--font-geist-sans)]">
      <main className="max-w-4xl mx-auto space-y-8">

        {/* Header */}
        <header className="flex justify-between items-center mb-8">
          <div>
            <h1 className="text-3xl font-bold tracking-tight text-slate-900 dark:text-slate-100">FeedFlux</h1>
            <p className="text-slate-500 dark:text-slate-400 mt-1">Your Intelligent Email Digest</p>
          </div>
          <div className="flex gap-2">
            <Button
              variant="outline"
              onClick={loadFeed}
              disabled={loading}
            >
              {loading ? 'Loading...' : 'Refresh'}
            </Button>
            <Button disabled>Generate Digest</Button>
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
                <Button onClick={loadFeed} variant="outline">Try Again</Button>
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
            feed.map((item) => (
              <Card
                key={item.id}
                className="hover:shadow-md transition-shadow cursor-pointer border-l-4 border-l-transparent hover:border-l-indigo-500"
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
                <CardContent>
                  <p className="text-sm text-slate-600 dark:text-slate-300 line-clamp-3">
                    {item.body_preview}
                  </p>
                </CardContent>
                <CardFooter className="flex justify-end pt-0">
                  <Button
                    variant="ghost"
                    size="sm"
                    className="text-indigo-600 hover:text-indigo-700 hover:bg-indigo-50"
                  >
                    Summarize with AI →
                  </Button>
                </CardFooter>
              </Card>
            ))
          )}
        </div>
      </main>
    </div>
  );
}
