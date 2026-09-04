"use client";

/**
 * ShareMenu — popover with Copy Link, Twitter, LinkedIn, WhatsApp.
 *
 * Implementation: a native `<details>`/`<summary>` for the open/close
 * toggle so we don't need a state hook + outside-click handler. The
 * popover panel renders at z-50, the toggle is a ghost button.
 *
 * On copy: writes `reel.url` to the clipboard and fires a toast.
 *
 * Z-index: z-50 puts the popover above the gallery grid but below
 * toasts (z-[100]). The ShareMenu's own button does NOT conflict with
 * the delete-confirm modal (z-40 in PR 3).
 */

import { useEffect, useRef } from "react";
import { Check, Copy, Link2, Linkedin, Twitter, X, Share2 } from "lucide-react";

import { useToast } from "@/components/Toast";
import { cn } from "@/lib/cn";

interface ShareMenuProps {
  /** The URL to share. Should be the signed reel URL. */
  url: string;
  /** Used in the Twitter/LinkedIn prefilled text. */
  title?: string;
  /** Optional className for the outer wrapper. */
  className?: string;
}

export function ShareMenu({ url, title, className }: ShareMenuProps) {
  const { success, error: toastError } = useToast();
  const detailsRef = useRef<HTMLDetailsElement | null>(null);

  // Close the popover when the user clicks outside of it.
  useEffect(() => {
    const handle = (e: MouseEvent) => {
      const el = detailsRef.current;
      if (!el) return;
      if (!el.contains(e.target as Node)) {
        el.removeAttribute("open");
      }
    };
    document.addEventListener("mousedown", handle);
    return () => document.removeEventListener("mousedown", handle);
  }, []);

  const handleCopy = async () => {
    try {
      await navigator.clipboard.writeText(url);
      success("Link copied!");
      detailsRef.current?.removeAttribute("open");
    } catch {
      // Fallback: prompt-based copy for older browsers
      try {
        const ta = document.createElement("textarea");
        ta.value = url;
        ta.style.position = "fixed";
        ta.style.opacity = "0";
        document.body.appendChild(ta);
        ta.select();
        document.execCommand("copy");
        document.body.removeChild(ta);
        success("Link copied!");
        detailsRef.current?.removeAttribute("open");
      } catch {
        toastError("Couldn't copy link — please copy it manually");
      }
    }
  };

  const tweetText = title ? `${title} — made with AI Reels Generator` : "Made with AI Reels Generator";
  const twitterHref = `https://twitter.com/intent/tweet?url=${encodeURIComponent(url)}&text=${encodeURIComponent(tweetText)}`;
  const linkedInHref = `https://www.linkedin.com/sharing/share-offsite/?url=${encodeURIComponent(url)}`;
  const whatsAppHref = `https://wa.me/?text=${encodeURIComponent(`${tweetText} ${url}`)}`;

  return (
    <details
      ref={detailsRef}
      className={cn("relative", className)}
    >
      <summary
        className="list-none cursor-pointer p-2.5 bg-white/5 hover:bg-white/10 border border-white/10 rounded-full transition-colors flex items-center justify-center"
        aria-label="Share reel"
        // Remove the default disclosure triangle on Safari
        style={{ listStyle: "none" }}
      >
        <Share2 size={16} className="text-white" />
      </summary>

      <div
        className="absolute right-0 top-full mt-2 w-56 rounded-2xl bg-dark border border-white/10 shadow-2xl z-50 p-1.5 animate-toast-in"
        // The summary click can sometimes leave the popover un-clickable
        // on iOS; this keeps it interactive.
        onClick={(e) => e.stopPropagation()}
      >
        <button
          type="button"
          onClick={handleCopy}
          className="w-full flex items-center gap-3 px-3 py-2 rounded-xl text-sm text-gray-200 hover:bg-white/5 transition-colors text-left"
        >
          <Copy size={16} className="text-gray-400" />
          Copy link
        </button>

        <a
          href={twitterHref}
          target="_blank"
          rel="noopener noreferrer"
          className="w-full flex items-center gap-3 px-3 py-2 rounded-xl text-sm text-gray-200 hover:bg-white/5 transition-colors"
          onClick={() => detailsRef.current?.removeAttribute("open")}
        >
          <Twitter size={16} className="text-gray-400" />
          Share on Twitter
        </a>

        <a
          href={linkedInHref}
          target="_blank"
          rel="noopener noreferrer"
          className="w-full flex items-center gap-3 px-3 py-2 rounded-xl text-sm text-gray-200 hover:bg-white/5 transition-colors"
          onClick={() => detailsRef.current?.removeAttribute("open")}
        >
          <Linkedin size={16} className="text-gray-400" />
          Share on LinkedIn
        </a>

        <a
          href={whatsAppHref}
          target="_blank"
          rel="noopener noreferrer"
          className="w-full flex items-center gap-3 px-3 py-2 rounded-xl text-sm text-gray-200 hover:bg-white/5 transition-colors"
          onClick={() => detailsRef.current?.removeAttribute("open")}
        >
          <Link2 size={16} className="text-gray-400" />
          Share on WhatsApp
        </a>
      </div>
    </details>
  );
}

export default ShareMenu;

// Re-export the icons for convenience (kept for any future consumers).
export { Check, Copy, X };
