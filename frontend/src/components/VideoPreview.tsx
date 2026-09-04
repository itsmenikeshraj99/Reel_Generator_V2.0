"use client";

/**
 * VideoPreview — 9:16 video frame with a gradient placeholder.
 *
 * Behaviour:
 *   - Default (static): shows the gradient + Play icon. NO autoplay, so
 *     we don't blow through the user's bandwidth on a 9-reel grid.
 *   - On hover: cross-fade in the actual <video> with `controls`.
 *   - Tap on mobile: the controls appear because the <video> has
 *     `controls preload="metadata"` and the browser handles native UX.
 *
 * Why a separate component: it lets ReelCard stay small and lets the
 * gallery swap the preview strategy (e.g. add autoplay on click) without
 * touching the card itself.
 */

import { Play } from "lucide-react";
import { useState } from "react";

interface VideoPreviewProps {
  src: string;
  /** Optional class for the outer frame. */
  className?: string;
}

export function VideoPreview({ src, className }: VideoPreviewProps) {
  const [hover, setHover] = useState(false);

  return (
    <div
      className={`relative aspect-[9/16] bg-gradient-to-br from-primary/20 to-secondary/20 overflow-hidden ${className ?? ""}`}
      onMouseEnter={() => setHover(true)}
      onMouseLeave={() => setHover(false)}
    >
      {/* Static placeholder: gradient + Play icon */}
      <div
        className={`absolute inset-0 flex items-center justify-center transition-opacity duration-300 ${
          hover ? "opacity-0" : "opacity-100"
        }`}
      >
        <div className="p-4 rounded-full bg-black/30 backdrop-blur-sm">
          <Play size={36} className="text-white/90" fill="currentColor" />
        </div>
      </div>

      {/* Video on hover */}
      <video
        src={src}
        controls={hover}
        loop
        playsInline
        preload="metadata"
        muted
        className={`absolute inset-0 w-full h-full object-cover bg-black transition-opacity duration-300 ${
          hover ? "opacity-100" : "opacity-0"
        }`}
      />
    </div>
  );
}

export default VideoPreview;
