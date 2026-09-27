import type { Metadata } from "next";
import type { ComponentProps } from "react";
import { Geist, Geist_Mono } from "next/font/google";
import { Toaster } from "sonner";
import { TOASTER_OPTIONS } from "@/lib/notification-policy.mjs";
import "./globals.css";

const toasterOptions = TOASTER_OPTIONS as ComponentProps<typeof Toaster>;

const geistSans = Geist({
  variable: "--font-geist-sans",
  subsets: ["latin"],
});

const geistMono = Geist_Mono({
  variable: "--font-geist-mono",
  subsets: ["latin"],
});

export const metadata: Metadata = {
  title: "FeedFlux - Your Intelligent Email Digest",
  description: "AI-powered email summarization with context-aware insights",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en">
      <body
        className={`${geistSans.variable} ${geistMono.variable} antialiased`}
      >
        {children}
        <Toaster {...toasterOptions} />
      </body>
    </html>
  );
}
