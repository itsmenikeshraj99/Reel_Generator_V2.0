"use client";

/**
 * Skeleton placeholders. Pure CSS shimmer — no JS, no images.
 *
 * Variants:
 *   - "text": single line of text (height: 1em, full width by default)
 *   - "rect": rounded block (use for cards / images)
 *   - "circle": round (use for avatars)
 */

import { cn } from "@/lib/cn";

type SkeletonVariant = "text" | "rect" | "circle";

interface SkeletonProps {
  variant?: SkeletonVariant;
  width?: string | number;
  height?: string | number;
  className?: string;
}

export function Skeleton({
  variant = "rect",
  width,
  height,
  className,
}: SkeletonProps) {
  const shape =
    variant === "circle"
      ? "rounded-full"
      : variant === "text"
      ? "rounded-md h-3"
      : "rounded-xl";

  return (
    <div
      aria-hidden="true"
      style={{
        width: typeof width === "number" ? `${width}px` : width,
        height: typeof height === "number" ? `${height}px` : height,
      }}
      className={cn(
        "bg-white/5 animate-pulse",
        shape,
        className,
      )}
    />
  );
}
