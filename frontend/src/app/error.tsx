"use client";

export default function GlobalError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  return (
    <div className="min-h-screen bg-bg text-text flex flex-col items-center justify-center p-6">
      <div className="max-w-md text-center space-y-4">
        <h1 className="text-3xl font-bold">Something went wrong</h1>
        <p className="text-text-muted">
          {error.message || "An unexpected error occurred."}
        </p>
        {error.digest && (
          <p className="text-xs text-text-subtle">Error ID: {error.digest}</p>
        )}
        <button
          onClick={reset}
          className="bg-gradient-to-r from-primary to-secondary text-white px-6 py-3 rounded-full font-bold"
        >
          Try again
        </button>
      </div>
    </div>
  );
}
