"use client";

/**
 * useTheme — Phase 12 PR 3.
 *
 * Reads the user's saved theme from localStorage on mount, sets the
 * `data-theme` attribute on <html>, and exposes a `toggle` for the
 * ThemeToggle button. Default is "dark" to match the current visual.
 *
 * No SSR: first paint is always dark (matches the SSR-rendered
 * <html data-theme="dark">), then a useEffect flips if the saved
 * preference is "light". The inline script in app/layout.tsx handles
 * the no-flash case — by the time React mounts, the attribute is
 * already correct, so the initial useState("dark") is just a fallback
 * that immediately gets corrected if needed.
 */

import { useCallback, useEffect, useState } from "react";

export type Theme = "dark" | "light";
const STORAGE_KEY = "reelgen-theme";

function readStored(): Theme {
  if (typeof window === "undefined") return "dark";
  const v = localStorage.getItem(STORAGE_KEY);
  return v === "light" ? "light" : "dark";
}

export function useTheme() {
  const [theme, setThemeState] = useState<Theme>("dark");

  // Read once after mount; the inline script in layout.tsx has already
  // set <html data-theme> to the saved value, so this is a no-op when
  // preferences match and a self-heal when they don't.
  useEffect(() => {
    const stored = readStored();
    setThemeState(stored);
    document.documentElement.dataset.theme = stored;
  }, []);

  const setTheme = useCallback((next: Theme) => {
    setThemeState(next);
    document.documentElement.dataset.theme = next;
    try {
      localStorage.setItem(STORAGE_KEY, next);
    } catch {
      // localStorage may be blocked (private mode, etc.) — silently
      // ignore. The user can still toggle for the current session.
    }
  }, []);

  const toggle = useCallback(
    () => setTheme(theme === "dark" ? "light" : "dark"),
    [theme, setTheme],
  );

  return { theme, setTheme, toggle };
}
