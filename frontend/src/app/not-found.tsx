import Link from "next/link";

import AppShell from "@/components/AppShell";

export default function NotFound() {
  return (
    <AppShell showNav={false}>
      <div className="min-h-[80vh] flex flex-col items-center justify-center p-6 text-center">
        <h1 className="text-7xl font-extrabold bg-gradient-to-r from-primary to-secondary bg-clip-text text-transparent">
          404
        </h1>
        <p className="text-gray-300 mt-4 text-lg">Page not found</p>
        <p className="text-gray-500 mt-1 text-sm max-w-md">
          The page you're looking for doesn't exist or may have been moved.
        </p>
        <div className="flex items-center gap-3 mt-8 flex-wrap justify-center">
          <Link
            href="/"
            className="bg-white text-black px-5 py-2.5 rounded-full font-semibold text-sm hover:bg-gray-200 transition-colors"
          >
            Go home
          </Link>
          <Link
            href="/dashboard"
            className="bg-white/5 hover:bg-white/10 border border-white/10 text-white px-5 py-2.5 rounded-full font-semibold text-sm transition-colors"
          >
            Browse dashboard
          </Link>
        </div>
      </div>
    </AppShell>
  );
}
