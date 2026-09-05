import Link from "next/link";

import AppShell from "@/components/AppShell";

export default function NotFound() {
  return (
    <AppShell showNav={false}>
      <div className="min-h-[80vh] flex flex-col items-center justify-center p-6 text-center">
        <h1 className="text-7xl font-extrabold bg-gradient-to-r from-primary to-secondary bg-clip-text text-transparent">
          404
        </h1>
        <p className="text-text-muted mt-4 text-lg">Page not found</p>
        <p className="text-text-subtle mt-1 text-sm max-w-md">
          The page you're looking for doesn't exist or may have been moved.
        </p>
        <div className="flex items-center gap-3 mt-8 flex-wrap justify-center">
          <Link
            href="/"
            className="bg-text text-bg px-5 py-2.5 rounded-full font-semibold text-sm hover:opacity-90 transition-colors"
          >
            Go home
          </Link>
          <Link
            href="/dashboard"
            className="bg-black/5 hover:bg-black/10 border border-border text-text px-5 py-2.5 rounded-full font-semibold text-sm transition-colors"
          >
            Browse dashboard
          </Link>
        </div>
      </div>
    </AppShell>
  );
}
