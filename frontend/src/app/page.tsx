'use client';

import { useEffect, useState } from 'react';
import { Card, CardContent, CardDescription, CardFooter, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";

interface FeedItem {
  id: string;
  subject: string;
  sender: string;
  received_datetime: string;
  body_preview: string;
}

export default function Home() {
  const [feed, setFeed] = useState<FeedItem[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    async function fetchFeed() {
      try {
        const res = await fetch('/api/feed?limit=10');
        if (!res.ok) throw new Error('Failed to fetch');
        const data = await res.json();
        setFeed(data);
      } catch (error) {
        console.error(error);
      } finally {
        setLoading(false);
      }
    }
    fetchFeed();
  }, []);

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
            <Button variant="outline">Refresh</Button>
            <Button>Generate Digest</Button>
          </div>
        </header>

        {/* Feed List */}
        <div className="space-y-4">
          {loading ? (
            // Skeletons
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
          ) : (
            feed.map((item) => (
              <Card key={item.id} className="hover:shadow-md transition-shadow cursor-pointer border-l-4 border-l-transparent hover:border-l-indigo-500">
                <CardHeader>
                  <div className="flex justify-between items-start">
                    <CardTitle className="text-xl font-semibold text-slate-800 dark:text-slate-100">{item.subject}</CardTitle>
                    <Badge variant="secondary" className="text-xs">{new Date(item.received_datetime).toLocaleDateString()}</Badge>
                  </div>
                  <CardDescription className="text-slate-500">{item.sender}</CardDescription>
                </CardHeader>
                <CardContent>
                  <p className="text-sm text-slate-600 dark:text-slate-300 line-clamp-3">
                    {item.body_preview}
                  </p>
                </CardContent>
                <CardFooter className="flex justify-end pt-0">
                  <Button variant="ghost" size="sm" className="text-indigo-600 hover:text-indigo-700 hover:bg-indigo-50">
                    Read & Summarize →
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
