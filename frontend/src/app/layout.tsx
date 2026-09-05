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
  icons: {
    icon: [
      { url: "/icon.png", sizes: "any", type: "image/png" },
      { url: "/favicon-40x40.png", sizes: "40x40", type: "image/png" },
    ],
    apple: { url: "/apple-icon.png", sizes: "180x180", type: "image/png" },
  },
};

export const viewport: Viewport = {
  themeColor: "#121212",
  width: "device-width",
  initialScale: 1,
};

// Phase 12 PR 2: inline script that runs before React hydrates to
// apply the saved theme. Prevents flash-of-dark when the user prefers
// light. Safe because it just reads localStorage and sets a single
// attribute on <html>; no network, no async, no user data exposure.
const themeScript = `
  try {
    var t = localStorage.getItem('reelgen-theme');
    if (t !== 'light' && t !== 'dark') t = 'dark';
    document.documentElement.setAttribute('data-theme', t);
  } catch (e) {}
`;

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    // `data-theme` is set inline by the script above before React
    // hydrates, so the server-rendered "dark" is only the SSR default;
    // the client value (from localStorage) takes over on first paint.
    <html lang="en" data-theme="dark" suppressHydrationWarning>
      <head>
        <script dangerouslySetInnerHTML={{ __html: themeScript }} />
      </head>
      <body className={inter.className}>
        <ToastProvider>{children}</ToastProvider>
      </body>
    </html>
  );
}
