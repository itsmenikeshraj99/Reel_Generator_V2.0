"use client";

/**
 * Empty-state placeholder for the dashboard, search, etc.
 *
 * Optional CTA button (Link with primary styling).
 */

import Link from "next/link";

interface EmptyStateProps {
  icon?: string; // emoji
  title: string;
  body?: string;
  ctaHref?: string;
  ctaLabel?: string;
}

export function EmptyState({
  icon = "📭",
  title,
  body,
  ctaHref,
  ctaLabel,
}: EmptyStateProps) {
  return (
    <div className="flex flex-col items-center justify-center text-center py-16 px-6 rounded-2xl border border-dashed border-white/10 bg-white/[0.02]">
      <div className="text-5xl mb-4" aria-hidden="true">
        {icon}
      </div>
      <h3 className="text-lg font-semibold text-white mb-1">{title}</h3>
      {body && <p className="text-sm text-gray-400 max-w-md mb-6">{body}</p>}
      {ctaHref && ctaLabel && (
        <Link
          href={ctaHref}
          className="bg-gradient-to-r from-primary to-secondary text-white px-5 py-2.5 rounded-full text-sm font-semibold hover:opacity-90 transition-opacity"
        >
          {ctaLabel}
        </Link>
      )}
    </div>
  );
}
