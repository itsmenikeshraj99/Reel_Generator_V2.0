import type { Metadata, Viewport } from "next";
import { Inter } from "next/font/google";

import "./globals.css";

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
    <html lang="en">
      <body className={inter.className}>{children}</body>
    </html>
  );
}
