"use client";

/**
 * OAuth callback route.
 *
 * When a user signs in with Google or GitHub, Supabase redirects them back
 * to the configured `redirectTo` URL with the session tokens in the URL
 * hash. We use this thin route as a "landing pad" for that redirect so
 * the session-detect logic runs in a stable location, and then we send
 * the user to the home page (or wherever they came from).
 *
 * `supabase.auth.detectSessionInUrl` (set in lib/supabase.ts) handles the
 * actual extraction of the tokens from the URL. By the time this page
 * mounts, the auth state listener in page.tsx will already have the new
 * session.
 */
import Link from "next/link";
import { useEffect, useState } from "react";
import { Loader2 } from "lucide-react";

import { supabase } from "@/lib/supabase";

export default function AuthCallback() {
  const [status, setStatus] = useState<"loading" | "ok" | "error">("loading");
  const [message, setMessage] = useState("Completing sign-in…");

  useEffect(() => {
    let cancelled = false;

    const complete = async () => {
      try {
        // detectSessionInUrl already parsed the URL hash into a session
        // when the client was first constructed. We just confirm a
        // session is now present.
        const { data, error } = await supabase.auth.getSession();
        if (cancelled) return;

        if (error) {
          setStatus("error");
          setMessage(error.message || "Sign-in failed");
          return;
        }
        if (!data.session) {
          setStatus("error");
          setMessage(
            "No session detected. The sign-in link may have expired or been used already.",
          );
          return;
        }

        setStatus("ok");
        setMessage(`Signed in as ${data.session.user.email}`);

        // Send the user to the home page after a brief moment so they
        // can see the confirmation. Bounce query param is honored if
        // the originating button passed one.
        const params = new URLSearchParams(window.location.search);
        const next = params.get("next") || "/";
        setTimeout(() => {
          if (!cancelled) window.location.replace(next);
        }, 800);
      } catch (err) {
        if (cancelled) return;
        setStatus("error");
        setMessage(
          err instanceof Error ? err.message : "Unexpected error during sign-in",
        );
      }
    };

    complete();
    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <main className="min-h-screen bg-dark text-white flex items-center justify-center p-4">
      <div className="text-center max-w-md space-y-4">
        {status === "loading" && (
          <>
            <Loader2 className="animate-spin mx-auto" size={40} />
            <p className="text-gray-400">{message}</p>
          </>
        )}

        {status === "ok" && (
          <>
            <div className="text-5xl">✅</div>
            <h1 className="text-2xl font-bold">Welcome!</h1>
            <p className="text-gray-400">{message}</p>
            <p className="text-sm text-gray-500">Redirecting…</p>
          </>
        )}

        {status === "error" && (
          <>
            <div className="text-5xl">⚠️</div>
            <h1 className="text-2xl font-bold">Sign-in Failed</h1>
            <p className="text-gray-400">{message}</p>
            <Link
              href="/"
              className="inline-block bg-white text-black px-6 py-2 rounded-full font-bold hover:bg-gray-200 transition-all"
            >
              Back to Home
            </Link>
          </>
        )}
      </div>
    </main>
  );
}
