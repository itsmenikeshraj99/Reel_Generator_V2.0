"use client";

/**
 * ProgressBar — thin gradient bar with a percentage label.
 *
 * Variants:
 *   - "indeterminate": pulses left-to-right (no value needed)
 *   - "value" (default): fills 0-100 with a smooth transition
 *
 * Why no `<progress>`: native progress bars are nearly impossible to
 * style across browsers and don't support gradients. A 30-line custom
 * bar looks 10x better for the same effort.
 */

import { cn } from "@/lib/cn";

interface ProgressBarProps {
  /** 0-100. Ignored if `indeterminate` is true. */
  value?: number;
  /** When true, shows the indeterminate animation. */
  indeterminate?: boolean;
  /** Optional label shown above the bar. */
  label?: string;
  /** Optional className for the outer wrapper. */
  className?: string;
}

export function ProgressBar({
  value = 0,
  indeterminate = false,
  label,
  className,
}: ProgressBarProps) {
  const pct = Math.max(0, Math.min(100, Math.round(value)));

  return (
    <div className={cn("w-full", className)}>
      {(label || !indeterminate) && (
        <div className="flex items-center justify-between text-xs text-text-muted mb-1.5">
          {label && <span>{label}</span>}
          {!indeterminate && <span>{pct}%</span>}
        </div>
      )}
      <div
        role="progressbar"
        aria-valuenow={indeterminate ? undefined : pct}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-label={label || "Progress"}
        className="h-1.5 w-full bg-black/10 rounded-full overflow-hidden"
      >
        <div
          className={cn(
            "h-full rounded-full transition-all duration-300 ease-out",
            "bg-gradient-to-r from-primary to-secondary",
            indeterminate && "animate-pulse-stage w-full",
          )}
          style={indeterminate ? undefined : { width: `${pct}%` }}
        />
      </div>
    </div>
  );
}

export default ProgressBar;
