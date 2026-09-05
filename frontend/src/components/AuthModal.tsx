"use client";

/**
 * AuthModal — Phase 12 PR 4.
 *
 * Visual + behavior changes from Phase 11:
 * - New "Full name" field on sign-up (optional, threaded into
 *   user.user_metadata.full_name via signUp options.data).
 * - Glass surface: bg-surface/80 backdrop-blur-2xl + gradient border feel
 *   via the inner logo + gradient ring.
 * - 2-column OAuth grid (saves vertical space).
 * - Color tokens for borders/text so it themes correctly with the
 *   day/night toggle introduced in PR 2/3.
 *
 * Existing behavior preserved:
 * - Email verification flow (signup → check inbox).
 * - ToS gate on signup only.
 * - OAuth popup-closed friendly error.
 * - 8-char password minimum.
 * - emailRedirectTo lands on /dashboard.
 */

import { useState } from "react";
import { Loader2, Lock, Mail, User, X } from "lucide-react";

import { supabase } from "@/lib/supabase";

type OAuthProvider = "google" | "github";

const AuthModal = ({
  isOpen,
  onClose,
}: {
  isOpen: boolean;
  onClose: () => void;
}) => {
  const [isSignUp, setIsSignUp] = useState(false);
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [loading, setLoading] = useState(false);
  const [oauthProvider, setOauthProvider] = useState<OAuthProvider | null>(
    null,
  );
  const [error, setError] = useState<string | null>(null);
  const [info, setInfo] = useState<string | null>(null);
  const [acceptedToS, setAcceptedToS] = useState(false);

  if (!isOpen) return null;

  const handleAuth = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    setInfo(null);

    if (password.length < 8) {
      setError("Password must be at least 8 characters.");
      return;
    }

    setLoading(true);
    try {
      if (isSignUp) {
        if (!acceptedToS) {
          setError("Please accept the Terms of Service.");
          setLoading(false);
          return;
        }
        // Phase 12 PR 4: thread name into user_metadata so AppShell can
        // show "Welcome, {name}" instead of the email address.
        // Optional — Supabase accepts `data: { full_name: undefined }`.
        const trimmedName = name.trim();
        const { error: signUpError } = await supabase.auth.signUp({
          email,
          password,
          options: {
            emailRedirectTo: `${window.location.origin}/auth/callback?next=/dashboard`,
            data: { full_name: trimmedName || undefined },
          },
        });
        if (signUpError) throw signUpError;
        setInfo("Verification email sent! Please check your inbox.");
      } else {
        const { error: signInError } = await supabase.auth.signInWithPassword({
          email,
          password,
        });
        if (signInError) throw signInError;
        onClose();
      }
    } catch (err) {
      const message = err instanceof Error ? err.message : "Authentication failed";
      setError(message);
    } finally {
      setLoading(false);
    }
  };

  const handleOAuth = async (provider: OAuthProvider) => {
    setError(null);
    setInfo(null);
    setOauthProvider(provider);
    try {
      const { error } = await supabase.auth.signInWithOAuth({
        provider,
        options: {
          redirectTo: `${window.location.origin}/auth/callback?next=/`,
        },
      });
      if (error) throw error;
    } catch (err) {
      const raw = err instanceof Error ? err.message : "Sign-in failed";
      if (/popup|closed|user_cancelled/i.test(raw)) {
        setError("Sign-in cancelled. Please try again.");
      } else {
        setError(raw);
      }
      setOauthProvider(null);
    }
  };

  const toggleMode = () => {
    setIsSignUp(!isSignUp);
    setError(null);
    setInfo(null);
    setName("");
  };

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-labelledby="auth-modal-title"
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 backdrop-blur-md p-4"
    >
      <div className="bg-surface/80 backdrop-blur-2xl text-text w-full max-w-md rounded-3xl overflow-hidden shadow-2xl border border-border relative">
        <button
          onClick={onClose}
          aria-label="Close"
          className="absolute top-4 right-4 p-1.5 rounded-full text-text-muted hover:text-text hover:bg-surface-2 transition-colors"
        >
          <X size={18} />
        </button>

        <div className="p-8">
          {/* Header: app logo on a gradient ring + title + subhead */}
          <div className="text-center mb-8">
            <div className="inline-flex items-center justify-center w-16 h-16 rounded-full bg-gradient-to-br from-primary to-secondary p-[2px] mb-4">
              <div className="w-full h-full rounded-full bg-surface flex items-center justify-center overflow-hidden">
                <img
                  src="/android-chrome-192x192.png"
                  alt="Reel Generator"
                  width={48}
                  height={48}
                  className="rounded-full"
                />
              </div>
            </div>
            <h2
              id="auth-modal-title"
              className="text-2xl font-bold bg-gradient-to-r from-primary to-secondary bg-clip-text text-transparent"
            >
              {isSignUp ? "Create Account" : "Welcome Back"}
            </h2>
            <p className="text-text-muted mt-2 text-sm">
              {isSignUp
                ? "Start creating viral reels today"
                : "Sign in to continue your session"}
            </p>
          </div>

          {/* OAuth: 2-column grid (Google + GitHub side-by-side) */}
          <div className="grid grid-cols-2 gap-3 mb-6">
            <button
              onClick={() => handleOAuth("google")}
              disabled={oauthProvider !== null}
              className="flex items-center justify-center gap-2 p-2.5 rounded-xl bg-surface-2/60 hover:bg-surface-2 border border-border transition-all disabled:opacity-50 disabled:cursor-wait"
              type="button"
              aria-label="Continue with Google"
            >
              {oauthProvider === "google" ? (
                <Loader2 className="animate-spin text-text-muted" size={18} />
              ) : (
                <svg className="w-5 h-5" viewBox="0 0 24 24" aria-hidden="true">
                  <path
                    fill="#4285F4"
                    d="M22.56 12.25c0-.78-.07-1.53-.2-2.25H12v4.26h5.92c-.26 1.37-1.04 2.53-2.21 3.31v2.77h3.57c2.08-1.92 3.28-4.74 3.28-8.09z"
                  />
                  <path
                    fill="#34A853"
                    d="M12 23c2.97 0 5.46-.98 7.28-2.66l-3.57-2.77c-.98.66-2.23 1.06-3.71 1.06-2.86 0-5.29-1.93-6.16-4.53H2.18v2.84C3.99 20.53 7.7 23 12 23z"
                  />
                  <path
                    fill="#FBBC05"
                    d="M5.84 14.09c-.22-.66-.35-1.36-.35-2.09s.13-1.43.35-2.09V7.07H2.18C1.43 8.55 1 10.22 1 12s.43 3.45 1.18 4.93l2.85-2.22.81-.62z"
                  />
                  <path
                    fill="#EA4335"
                    d="M12 5.38c1.62 0 3.06.56 4.21 1.64l3.15-3.15C17.45 2.09 14.97 1 12 1 7.7 1 3.99 3.47 2.18 7.07l3.66 2.84c.87-2.6 3.3-4.53 6.16-4.53z"
                  />
                </svg>
              )}
              <span className="text-sm font-medium text-text">
                {oauthProvider === "google" ? "…" : "Google"}
              </span>
            </button>

            <button
              onClick={() => handleOAuth("github")}
              disabled={oauthProvider !== null}
              className="flex items-center justify-center gap-2 p-2.5 rounded-xl bg-surface-2/60 hover:bg-surface-2 border border-border transition-all disabled:opacity-50 disabled:cursor-wait"
              type="button"
              aria-label="Continue with GitHub"
            >
              {oauthProvider === "github" ? (
                <Loader2 className="animate-spin text-text-muted" size={18} />
              ) : (
                <svg
                  className="w-5 h-5 text-text-muted"
                  viewBox="0 0 24 24"
                  fill="currentColor"
                  aria-hidden="true"
                >
                  <path d="M12 .297c-6.63 0-12 5.373-12 12 0 5.303 3.438 9.8 8.205 11.385.6.113.82-.258.82-.577 0-.285-.01-1.04-.015-2.04-3.338.724-4.042-1.61-4.042-1.61C4.422 18.07 3.633 17.7 3.633 17.7c-1.087-.744.084-.729.084-.729 1.205.084 1.838 1.236 1.838 1.236 1.07 1.835 2.809 1.305 3.495.998.108-.776.417-1.305.76-1.605-2.665-.3-5.466-1.332-5.466-5.93 0-1.31.465-2.38 1.235-3.22-.135-.303-.54-1.523.105-3.176 0 0 1.005-.322 3.3 1.23.96-.267 1.98-.4 3-.405 1.02.005 2.04.138 3 .405 2.28-1.552 3.285-1.23 3.285-1.23.645 1.653.24 2.873.12 3.176.765.84 1.23 1.91 1.23 3.22 0 4.61-2.805 5.625-5.475 5.92.42.36.81 1.096.81 2.22 0 1.606-.015 2.896-.015 3.286 0 .315.21.69.825.57C20.565 22.092 24 17.592 24 12.297c0-6.627-5.373-12-12-12" />
                </svg>
              )}
              <span className="text-sm font-medium text-text">
                {oauthProvider === "github" ? "…" : "GitHub"}
              </span>
            </button>
          </div>

          {/* Divider */}
          <div className="relative mb-6">
            <div className="absolute inset-0 flex items-center">
              <span className="w-full border-t border-border" />
            </div>
            <div className="relative flex justify-center text-[10px] uppercase tracking-widest">
              <span className="bg-surface px-2 text-text-subtle">
                Or use email
              </span>
            </div>
          </div>

          <form onSubmit={handleAuth} className="space-y-4">
            {isSignUp && (
              <div className="relative">
                <User
                  className="absolute left-3 top-1/2 -translate-y-1/2 text-text-subtle"
                  size={18}
                />
                <input
                  type="text"
                  placeholder="Your name (optional)"
                  className="w-full bg-surface-2/60 border border-border rounded-xl py-2.5 pl-10 pr-4 text-sm text-text placeholder:text-text-subtle focus:border-primary focus:ring-2 focus:ring-primary/30 outline-none transition-all"
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                  autoComplete="name"
                  maxLength={80}
                />
              </div>
            )}

            <div className="relative">
              <Mail
                className="absolute left-3 top-1/2 -translate-y-1/2 text-text-subtle"
                size={18}
              />
              <input
                type="email"
                placeholder="Email address"
                className="w-full bg-surface-2/60 border border-border rounded-xl py-2.5 pl-10 pr-4 text-sm text-text placeholder:text-text-subtle focus:border-primary focus:ring-2 focus:ring-primary/30 outline-none transition-all"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                required
                autoComplete="email"
                maxLength={254}
              />
            </div>

            <div className="relative">
              <Lock
                className="absolute left-3 top-1/2 -translate-y-1/2 text-text-subtle"
                size={18}
              />
              <input
                type="password"
                placeholder="Password (min 8 chars)"
                className="w-full bg-surface-2/60 border border-border rounded-xl py-2.5 pl-10 pr-4 text-sm text-text placeholder:text-text-subtle focus:border-primary focus:ring-2 focus:ring-primary/30 outline-none transition-all"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                required
                minLength={8}
                autoComplete={isSignUp ? "new-password" : "current-password"}
              />
            </div>

            {isSignUp && (
              <label className="flex items-start gap-3 cursor-pointer group">
                <input
                  type="checkbox"
                  className="mt-1 rounded border-border bg-surface-2 text-primary focus:ring-primary accent-primary"
                  checked={acceptedToS}
                  onChange={(e) => setAcceptedToS(e.target.checked)}
                />
                <span className="text-xs text-text-muted group-hover:text-text transition-colors">
                  I agree to the{" "}
                  <a
                    href="/legal/terms"
                    className="text-secondary underline hover:text-secondary/80"
                  >
                    Terms of Service
                  </a>{" "}
                  and content-ownership policies.
                </span>
              </label>
            )}

            {error && (
              <div
                role="alert"
                className="p-3 rounded-lg bg-red-500/10 border border-red-500/20 text-red-400 text-xs text-center"
              >
                {error}
              </div>
            )}

            {info && (
              <div
                role="status"
                className="p-3 rounded-lg bg-green-500/10 border border-green-500/20 text-green-400 text-xs text-center"
              >
                {info}
              </div>
            )}

            <button
              type="submit"
              disabled={loading}
              className="w-full bg-gradient-to-r from-primary to-secondary text-white font-bold py-2.5 rounded-xl hover:opacity-90 transition-opacity disabled:opacity-50 flex items-center justify-center gap-2"
            >
              {loading && <Loader2 className="animate-spin" size={18} />}
              {loading
                ? "Processing…"
                : isSignUp
                  ? "Sign Up"
                  : "Sign In"}
            </button>
          </form>

          <div className="text-center mt-6">
            <button
              onClick={toggleMode}
              className="text-sm text-text-muted hover:text-text transition-colors"
              type="button"
            >
              {isSignUp
                ? "Already have an account? Sign In"
                : "Don't have an account? Sign Up"}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
};

export default AuthModal;
