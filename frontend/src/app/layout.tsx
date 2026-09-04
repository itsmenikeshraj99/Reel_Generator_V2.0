import type { Metadata, Viewport } from "next";
import { Inter } from "next/font/google";

import "./globals.css";
import { ToastProvider } from "@/components/Toast";

const inter = Inter({ subsets: ["latin"] });

export const metadata: Metadata = {
  title: "AI Reels Generator",
  description: "Turn long videos into viral short-form reels with AI.",
  applicationName: "AI Reels Generator",
  authors: [{ name: "AI Reels Generator" }],
  openGraph: {
    title: "AI Reels Generator",
    description: "Turn long videos into viral short-form reels with AI.",
    type: "website",
  },
  twitter: {
    card: "summary_large_image",
    title: "AI Reels Generator",
    description: "Turn long videos into viral short-form reels with AI.",
  },
  robots: { index: true, follow: true },
};

export const viewport: Viewport = {
  themeColor: "#121212",
  width: "device-width",
  initialScale: 1,
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    // `data-theme` is set for future light-mode support; the app is
    // currently dark-only (Phase 11).
    <html lang="en" data-theme="dark" suppressHydrationWarning>
      <body className={inter.className}>
        <ToastProvider>{children}</ToastProvider>
      </body>
    </html>
  );
}
