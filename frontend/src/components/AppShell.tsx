"use client";

/**
 * AppShell — top nav + mobile drawer, used by every authenticated page.
 *
 * Auth: client-side via supabase.auth.onAuthStateChange. The page that
 * wraps itself in <AppShell> is responsible for its own auth gate; this
 * shell just shows the right chrome for the current user state.
 *
 * Why client (not server): the existing project uses client-side auth
 * gating (proxy.ts is a no-op, every page does getSession in useEffect).
 * A server-side check would require @supabase/ssr cookie setup — a much
 * bigger refactor. Defer to a future phase.
 */

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import type { User } from "@supabase/supabase-js";
import { LogOut, Menu, X } from "lucide-react";

import { supabase } from "@/lib/supabase";
import { useToast } from "@/components/Toast";
import { cn } from "@/lib/cn";

interface AppShellProps {
  children: React.ReactNode;
  /** When true, the top nav is rendered (default true). Useful for hiding on landing. */
  showNav?: boolean;
}

const NAV_LINKS: Array<{ href: string; label: string }> = [
  { href: "/dashboard", label: "Dashboard" },
  { href: "/upload", label: "Upload" },
];

export function AppShell({ children, showNav = true }: AppShellProps) {
  const router = useRouter();
  const pathname = usePathname();
  const { success, info } = useToast();

  const [user, setUser] = useState<User | null>(null);
  const [drawerOpen, setDrawerOpen] = useState(false);

  // Subscribe to auth state
  useEffect(() => {
    let active = true;
    supabase.auth.getSession().then(({ data: { session } }) => {
      if (active) setUser(session?.user ?? null);
    });
    const { data: { subscription } } = supabase.auth.onAuthStateChange(
      (_event, session) => setUser(session?.user ?? null),
    );
    return () => {
      active = false;
      subscription.unsubscribe();
    };
  }, []);

  // Close drawer on route change
  useEffect(() => {
    setDrawerOpen(false);
  }, [pathname]);

  // Lock body scroll while drawer is open
  useEffect(() => {
    if (!drawerOpen) return;
    const prev = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.body.style.overflow = prev;
    };
  }, [drawerOpen]);

  const handleSignOut = useCallback(async () => {
    await supabase.auth.signOut();
    setDrawerOpen(false);
    info("Signed out");
    router.push("/");
  }, [info, router]);

  // When a sign-in lands back on a protected page, share a toast
  // (PR 3 will move sign-in flows to redirect to /dashboard, but this
  //  stays useful for OAuth callbacks.)
  useEffect(() => {
    const flag = typeof window !== "undefined"
      ? sessionStorage.getItem("just-signed-in")
      : null;
    if (flag && user) {
      sessionStorage.removeItem("just-signed-in");
      success(flag);
    }
  }, [user, success]);

  return (
    <div className="min-h-screen bg-dark text-white flex flex-col">
      {showNav && (
        <nav className="sticky top-0 z-30 bg-dark/80 backdrop-blur-md border-b border-white/5">
          <div className="max-w-6xl mx-auto px-4 sm:px-6 h-16 flex items-center justify-between">
            <Link
              href="/"
              className="flex items-center gap-2 whitespace-nowrap hover:opacity-80 transition-opacity"
            >
              <img
                src="/android-chrome-192x192.png"
                alt="Reel Generator"
                width={32}
                height={32}
                className="w-8 h-8"
              />
              <span className="text-lg sm:text-xl font-extrabold bg-gradient-to-r from-primary to-secondary bg-clip-text text-transparent">
                Reel Generator
              </span>
            </Link>

            {/* Desktop links */}
            <div className="hidden md:flex items-center gap-1">
              {NAV_LINKS.map((link) => {
                const active = pathname === link.href ||
                  (link.href !== "/" && pathname?.startsWith(link.href + "/"));
                return (
                  <Link
                    key={link.href}
                    href={link.href}
                    className={cn(
                      "px-4 py-2 rounded-full text-sm font-medium transition-colors",
                      active
                        ? "bg-white/10 text-white"
                        : "text-gray-400 hover:text-white hover:bg-white/5",
                    )}
                  >
                    {link.label}
                  </Link>
                );
              })}
            </div>

            {/* Right side: sign in / out */}
            <div className="hidden md:flex items-center gap-3">
              {user ? (
                <>
                  <span className="text-xs text-gray-500 max-w-[180px] truncate">
                    {user.email}
                  </span>
                  <button
                    onClick={handleSignOut}
                    className="text-sm text-gray-400 hover:text-white transition-colors flex items-center gap-1.5"
                    aria-label="Sign out"
                  >
                    <LogOut size={14} />
                    Sign Out
                  </button>
                </>
              ) : (
                <Link
                  href="/"
                  className="text-sm text-gray-400 hover:text-white transition-colors"
                >
                  Sign In
                </Link>
              )}
            </div>

            {/* Mobile hamburger */}
            <button
              onClick={() => setDrawerOpen(true)}
              className="md:hidden p-2 -mr-2 text-gray-400 hover:text-white"
              aria-label="Open menu"
            >
              <Menu size={22} />
            </button>
          </div>
        </nav>
      )}

      <main className="flex-1">{children}</main>

      {/* Mobile drawer */}
      {drawerOpen && (
        <div
          className="md:hidden fixed inset-0 z-40"
          onClick={() => setDrawerOpen(false)}
        >
          <div
            className="absolute inset-0 bg-black/60 backdrop-blur-sm animate-toast-in"
            aria-hidden="true"
          />
          <aside
            className="absolute right-0 top-0 bottom-0 w-72 max-w-[85vw] bg-dark border-l border-white/10 p-6 flex flex-col gap-6 shadow-2xl"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="flex items-center justify-between">
              <span className="text-sm font-semibold text-gray-400 uppercase tracking-wider">
                Menu
              </span>
              <button
                onClick={() => setDrawerOpen(false)}
                className="p-1 text-gray-400 hover:text-white"
                aria-label="Close menu"
              >
                <X size={20} />
              </button>
            </div>

            <div className="flex flex-col gap-1">
              {NAV_LINKS.map((link) => {
                const active = pathname === link.href ||
                  (link.href !== "/" && pathname?.startsWith(link.href + "/"));
                return (
                  <Link
                    key={link.href}
                    href={link.href}
                    className={cn(
                      "px-4 py-3 rounded-xl text-sm font-medium transition-colors",
                      active
                        ? "bg-white/10 text-white"
                        : "text-gray-300 hover:text-white hover:bg-white/5",
                    )}
                  >
                    {link.label}
                  </Link>
                );
              })}
            </div>

            <div className="mt-auto pt-6 border-t border-white/10">
              {user ? (
                <>
                  <p className="text-xs text-gray-500 mb-3 truncate">
                    {user.email}
                  </p>
                  <button
                    onClick={handleSignOut}
                    className="w-full flex items-center justify-center gap-2 px-4 py-2.5 rounded-xl bg-white/5 hover:bg-white/10 text-sm text-gray-200"
                  >
                    <LogOut size={14} />
                    Sign Out
                  </button>
                </>
              ) : (
                <Link
                  href="/"
                  className="w-full block text-center px-4 py-2.5 rounded-xl bg-gradient-to-r from-primary to-secondary text-white text-sm font-semibold"
                  onClick={() => setDrawerOpen(false)}
                >
                  Sign In
                </Link>
              )}
            </div>
          </aside>
        </div>
      )}
    </div>
  );
}

export default AppShell;
