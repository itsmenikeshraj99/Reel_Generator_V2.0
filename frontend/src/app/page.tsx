"use client";

import type { User } from "@supabase/supabase-js";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";

import AppShell from "@/components/AppShell";
import AuthModal from "@/components/AuthModal";
import { Skeleton } from "@/components/Skeleton";
import { supabase } from "@/lib/supabase";

/**
 * /  — landing (logged-out) / bounce to /dashboard (logged-in).
 *
 * Phase 11 split: the dashboard moved to its own /dashboard route. The
 * landing page now ONLY renders when the user is logged out. If a
 * logged-in user lands on / we bounce them to /dashboard.
 */
export default function Home() {
  const router = useRouter();
  const [isAuthOpen, setIsAuthOpen] = useState(false);
  const [user, setUser] = useState<User | null>(null);
  const [authChecked, setAuthChecked] = useState(false);

  useEffect(() => {
    const checkUser = async () => {
      const {
        data: { session },
      } = await supabase.auth.getSession();
      const u = session?.user ?? null;
      setUser(u);
      setAuthChecked(true);
      if (u) {
        router.replace("/dashboard");
      }
    };
    checkUser();

    const {
      data: { subscription },
    } = supabase.auth.onAuthStateChange((_event, session) => {
      const u = session?.user ?? null;
      setUser(u);
      if (u) {
        router.replace("/dashboard");
      }
    });
    return () => subscription.unsubscribe();
  }, [router]);

  if (!authChecked) {
    return (
      <div className="min-h-screen bg-dark text-white flex items-center justify-center">
        <Skeleton width={120} height={40} className="rounded-full" />
      </div>
    );
  }

  // Bounce handled in useEffect; render a blank shell for the brief
  // moment between auth-check and navigation.
  if (user) {
    return (
      <AppShell showNav={false}>
        <div className="min-h-[60vh] flex items-center justify-center">
          <div className="animate-spin rounded-full h-10 w-10 border-t-2 border-primary" />
        </div>
      </AppShell>
    );
  }

  return (
    <main className="min-h-screen bg-dark text-white flex flex-col items-center justify-center p-4">
      <div className="text-center space-y-6 max-w-2xl">
        <img
          src="/logo-1024x1024.png"
          alt="Reel Generator logo"
          width={180}
          height={180}
          className="mx-auto w-32 h-32 sm:w-40 sm:h-40 md:w-44 md:h-44 drop-shadow-2xl"
        />
        <h1 className="text-5xl sm:text-6xl font-extrabold bg-gradient-to-r from-primary to-secondary bg-clip-text text-transparent">
          AI Reels Generator
        </h1>
        <p className="text-gray-400 text-lg sm:text-xl">
          Turn your long videos into viral short-form content in seconds.
        </p>
        <button
          onClick={() => setIsAuthOpen(true)}
          className="bg-white text-black px-8 py-4 rounded-full font-bold text-lg hover:bg-gray-200 transition-all scale-100 hover:scale-105 active:scale-95"
        >
          Get Started
        </button>
        <p className="text-xs text-gray-500 pt-4">
          Powered by Google Gemini • 24h sessions, no account data sold
        </p>
      </div>

      <AuthModal isOpen={isAuthOpen} onClose={() => setIsAuthOpen(false)} />
    </main>
  );
}
