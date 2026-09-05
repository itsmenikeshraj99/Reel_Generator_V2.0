"use client";

/**
 * ReelCard — one tile in the gallery grid.
 *
 * Layout:
 *   - 9:16 VideoPreview (gradient + Play by default, video on hover)
 *   - Title + reel index
 *   - Action row: Share (popover), Download (direct <a> with `download` attr)
 *
 * Why no per-card delete: the gallery only deletes the WHOLE video via
 * the "Finish & Clear Session" button. Adding per-card delete would
 * need a backend route and complicates the UI; the current "clear
 * everything" flow is cleaner for V1.
 */

import { Download } from "lucide-react";

import { VideoPreview } from "@/components/VideoPreview";
import { ShareMenu } from "@/components/ShareMenu";
import { cn } from "@/lib/cn";
import type { Reel } from "@/lib/api";

interface ReelCardProps {
  reel: Reel;
  index: number;
  className?: string;
}

export function ReelCard({ reel, index, className }: ReelCardProps) {
  const title = reel.title ?? `Reel ${index + 1}`;
  const filename = `reel-${index + 1}.mp4`;

  return (
    <div
      className={cn(
        "bg-black/5 border border-border rounded-3xl overflow-hidden group hover:border-primary/40 transition-all flex flex-col",
        className,
      )}
    >
      <div className="relative">
        <VideoPreview src={reel.url} />
        {/* Reel index badge */}
        <div className="absolute top-3 left-3 bg-black/60 backdrop-blur text-white text-[10px] font-semibold uppercase tracking-wider px-2 py-1 rounded-full">
          #{index + 1}
        </div>
      </div>

      <div className="p-4 flex justify-between items-center gap-3">
        <div className="min-w-0 flex-1">
          <h3
            className="font-semibold text-sm truncate"
            title={title}
          >
            {title}
          </h3>
          <p className="text-[11px] text-text-subtle mt-0.5">Viral Candidate #{index + 1}</p>
        </div>
        <div className="flex items-center gap-1.5 shrink-0">
          <ShareMenu url={reel.url} title={title} />
          <a
            href={reel.url}
            download={filename}
            className="p-2.5 bg-text text-bg rounded-full hover:opacity-90 transition-colors"
            aria-label={`Download ${title}`}
            title="Download"
          >
            <Download size={16} />
          </a>
        </div>
      </div>
    </div>
  );
}

export default ReelCard;
