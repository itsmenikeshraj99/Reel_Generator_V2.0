"use client";

/**
 * ThemeToggle — Phase 12 PR 3.
 *
 * Sun/Moon icon button. Click flips between dark and light, persists
 * to localStorage via useTheme. The icon shows the DESTINATION (Sun
 * when in dark mode = "click to go to light"), matching what GitHub
 * and most design systems do.
 *
 * `aria-label` and `title` both reflect the destination mode for
 * screen readers and tooltips.
 */

import { Moon, Sun } from "lucide-react";

import { useTheme } from "@/hooks/useTheme";
import { cn } from "@/lib/cn";

interface ThemeToggleProps {
  className?: string;
}

export function ThemeToggle({ className }: ThemeToggleProps) {
  const { theme, toggle } = useTheme();
  const isDark = theme === "dark";
  const destination = isDark ? "light" : "dark";
  return (
    <button
      type="button"
      onClick={toggle}
      aria-label={`Switch to ${destination} mode`}
      title={`Switch to ${destination} mode`}
      className={cn(
        "p-2.5 rounded-full transition-colors",
        "bg-black/5 hover:bg-black/10",
        "text-text-muted hover:text-text",
        className,
      )}
    >
      {isDark ? <Sun size={16} /> : <Moon size={16} />}
    </button>
  );
}

export default ThemeToggle;
