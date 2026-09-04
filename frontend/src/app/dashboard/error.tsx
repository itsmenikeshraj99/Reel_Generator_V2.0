"use client";

/**
 * Dashboard error boundary. Per Next.js 16 error.tsx convention, this
 * is rendered when an unhandled error bubbles up from the dashboard
 * route. The `reset` callback re-renders the segment.
 */

import { useEffect } from "react";
import { AlertCircle } from "lucide-react";

import AppShell from "@/components/AppShell";

export default function DashboardError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  useEffect(() => {
    // Log to console for now; Phase 12 will wire Sentry
    console.error("Dashboard error boundary:", error);
  }, [error]);

  return (
    <AppShell>
      <div className="max-w-6xl mx-auto px-4 sm:px-6 py-16">
        <div className="max-w-md mx-auto text-center">
          <AlertCircle size={48} className="text-red-400 mx-auto mb-4" />
          <h2 className="text-xl font-bold mb-2">Something went wrong</h2>
          <p className="text-sm text-gray-400 mb-6">
            {error.message || "An unexpected error occurred while loading your dashboard."}
          </p>
          <button
            onClick={reset}
            className="bg-gradient-to-r from-primary to-secondary text-white px-5 py-2.5 rounded-full text-sm font-semibold hover:opacity-90 transition-opacity"
          >
            Try again
          </button>
        </div>
      </div>
    </AppShell>
  );
}
