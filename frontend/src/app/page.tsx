"use client";

import type { User } from "@supabase/supabase-js";
import Link from "next/link";
import { useEffect, useState } from "react";

import AuthModal from "@/components/AuthModal";
import { supabase } from "@/lib/supabase";

export default function Home() {
  const [isAuthOpen, setIsAuthOpen] = useState(false);
  const [user, setUser] = useState<User | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    const checkUser = async () => {
      const {
        data: { session },
      } = await supabase.auth.getSession();
      setUser(session?.user ?? null);
      setLoading(false);
    };
    checkUser();

    const {
      data: { subscription },
    } = supabase.auth.onAuthStateChange((_event, session) => {
      setUser(session?.user ?? null);
    });
    return () => subscription.unsubscribe();
  }, []);

  if (loading) {
    return (
      <div className="min-h-screen bg-dark text-white flex items-center justify-center">
        <div className="animate-spin rounded-full h-12 w-12 border-t-2 border-primary" />
      </div>
    );
  }

  if (user) {
    return (
      <main className="min-h-screen bg-dark text-white p-4">
        <nav className="flex justify-between items-center max-w-6xl mx-auto py-6">
          <h1 className="text-2xl font-bold bg-gradient-to-r from-primary to-secondary bg-clip-text text-transparent">
            AI Reels Generator
          </h1>
          <button
            onClick={async () => {
              await supabase.auth.signOut();
              setUser(null);
            }}
            className="text-sm text-gray-400 hover:text-white transition-colors"
          >
            Sign Out
          </button>
        </nav>

        <div className="max-w-6xl mx-auto mt-20 text-center space-y-8">
          <h2 className="text-4xl font-bold">Welcome back, {user.email}! 👋</h2>
          <p className="text-gray-400 text-lg">
            Ready to create some viral content?
          </p>

          <div className="grid grid-cols-1 md:grid-cols-3 gap-6 mt-12">
            <Link
              href="/upload"
              className="p-8 rounded-3xl bg-white/5 border border-white/10 hover:bg-white/10 transition-all text-center group"
            >
              <div className="text-4xl mb-4 group-hover:scale-110 transition-transform">
                📤
              </div>
              <h3 className="text-xl font-bold mb-2">Upload Video</h3>
              <p className="text-gray-400 text-sm">
                Start a new reel generation process
              </p>
            </Link>
            <Link
              href="/upload/gallery"
              className="p-8 rounded-3xl bg-white/5 border border-white/10 hover:bg-white/10 transition-all text-center group"
            >
              <div className="text-4xl mb-4 group-hover:scale-110 transition-transform">
                🖼️
              </div>
              <h3 className="text-xl font-bold mb-2">My Gallery</h3>
              <p className="text-gray-400 text-sm">
                View and download your generated reels
              </p>
            </Link>
            <Link
              href="/upload/status"
              className="p-8 rounded-3xl bg-white/5 border border-white/10 hover:bg-white/10 transition-all text-center group"
            >
              <div className="text-4xl mb-4 group-hover:scale-110 transition-transform">
                ⚡
              </div>
              <h3 className="text-xl font-bold mb-2">Process Status</h3>
              <p className="text-gray-400 text-sm">
                Check the progress of your videos
              </p>
            </Link>
          </div>
        </div>
      </main>
    );
  }

  return (
    <main className="min-h-screen bg-dark text-white flex flex-col items-center justify-center p-4">
      <div className="text-center space-y-6 max-w-2xl">
        <h1 className="text-6xl font-extrabold bg-gradient-to-r from-primary to-secondary bg-clip-text text-transparent">
          AI Reels Generator
        </h1>
        <p className="text-gray-400 text-xl">
          Turn your long videos into viral short-form content in seconds.
        </p>
        <button
          onClick={() => setIsAuthOpen(true)}
          className="bg-white text-black px-8 py-4 rounded-full font-bold text-lg hover:bg-gray-200 transition-all scale-100 hover:scale-105 active:scale-95"
        >
          Get Started
        </button>
      </div>

      <AuthModal isOpen={isAuthOpen} onClose={() => setIsAuthOpen(false)} />
    </main>
  );
}
