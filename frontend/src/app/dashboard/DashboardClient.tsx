"use client";

/**
 * Dashboard client — fetches the caller's videos, renders the grid.
 *
 * Layout:
 *   - Top: "Upload New Video" CTA card (big gradient button → /upload)
 *   - Grid: 1-col mobile, 2-col sm, 3-col lg. Each card has:
 *       - 9:16 visual placeholder (gradient + emoji)
 *       - Status badge (color-coded per stage)
 *       - Reel-count badge (top-right) when reels exist
 *       - Filename + created time + "Xh left" expiry
 *       - Action buttons (always visible, on the card itself):
 *           * "View Generated Reels" — only if reel_count > 0
 *               → /upload/gallery?videoId=...
 *           * "Generate New Reels" — always present (re-runs pipeline)
 *               → POST /api/videos/{id}/process, then redirect to status
 *   - Empty state: shown when the user has no videos.
 *   - Error state: inline error with retry.
 *
 * 24h rule: backend sets `expires_at` on the videos row and a worker
 * cleanup task wipes expired rows + storage. While expires_at is in the
 * future, the card stays here. Once it crosses, the backend filters it
 * out of `GET /api/videos`.
 *
 * Auth: the page reads the Supabase session and redirects to "/" if
 * there's no user. The backend's getMyVideos() also requires auth, so
 * a stale client without a session would just get a 401 → we treat as
 * empty + show "sign in" link.
 */

import { useCallback, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import { Loader2, RefreshCw, Upload, AlertCircle, Sparkles, Eye } from "lucide-react";

import AppShell from "@/components/AppShell";
import { EmptyState } from "@/components/EmptyState";
import { Skeleton } from "@/components/Skeleton";
import { useToast } from "@/components/Toast";
import { api, type VideoListItem } from "@/lib/api";
import { supabase } from "@/lib/supabase";
import { cn } from "@/lib/cn";

const STATUS_BADGE: Record<string, { label: string; classes: string; pulse?: boolean }> = {
  PENDING_UPLOAD: { label: "Pending", classes: "bg-gray-500/15 text-gray-300 border-gray-500/20" },
  UPLOADED: { label: "Uploaded", classes: "bg-blue-500/15 text-blue-300 border-blue-500/20" },
  PROCESSING: {
    label: "Processing",
    classes: "bg-yellow-500/15 text-yellow-300 border-yellow-500/20",
    pulse: true,
  },
  READY: { label: "Ready", classes: "bg-green-500/15 text-green-300 border-green-500/20" },
  FAILED: { label: "Failed", classes: "bg-red-500/15 text-red-300 border-red-500/20" },
};

function formatExpiresIn(iso: string): string {
  if (!iso) return "—";
  const ms = new Date(iso).getTime() - Date.now();
  if (ms <= 0) return "expired";
  const hours = ms / (1000 * 60 * 60);
  if (hours < 1) return `${Math.max(1, Math.round(hours * 60))}m left`;
  return `${Math.round(hours)}h left`;
}

function formatCreatedAt(iso: string): string {
  if (!iso) return "";
  const d = new Date(iso);
  const now = new Date();
  const sameDay =
    d.getFullYear() === now.getFullYear() &&
    d.getMonth() === now.getMonth() &&
    d.getDate() === now.getDate();
  if (sameDay) {
    return d.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" });
  }
  return d.toLocaleDateString(undefined, { month: "short", day: "numeric" });
}

export default function DashboardClient() {
  const router = useRouter();
  const { success: toastSuccess, error: toastError } = useToast();

  const [authChecked, setAuthChecked] = useState(false);
  const [isAuthed, setIsAuthed] = useState(false);
  const [videos, setVideos] = useState<VideoListItem[] | null>(null);
  const [total, setTotal] = useState(0);
  const [fetchError, setFetchError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  // Per-video generation state. null = idle, true = generating.
  const [generating, setGenerating] = useState<Record<string, boolean>>({});

  const load = useCallback(async () => {
    setLoading(true);
    setFetchError(null);
    try {
      const res = await api.getMyVideos();
      setVideos(res.videos);
      setTotal(res.total);
    } catch (err) {
      const msg = err instanceof Error ? err.message : "Failed to load videos";
      setFetchError(msg);
      // 401 from the backend = session expired; bounce to landing
      if (/401|unauth/i.test(msg)) {
        router.replace("/?redirect=/dashboard");
        return;
      }
      toastError(msg);
    } finally {
      setLoading(false);
    }
  }, [router, toastError]);

  // Auth check on mount
  useEffect(() => {
    let cancelled = false;
    (async () => {
      const { data: { user } } = await supabase.auth.getUser();
      if (cancelled) return;
      if (!user) {
        router.replace("/?redirect=/dashboard");
        return;
      }
      setIsAuthed(true);
      setAuthChecked(true);
    })();
    return () => {
      cancelled = true;
    };
  }, [router]);

  // Once authed, fetch the videos
  useEffect(() => {
    if (!isAuthed) return;
    load();
  }, [isAuthed, load]);

  const handleGenerate = useCallback(
    async (videoId: string) => {
      setGenerating((g) => ({ ...g, [videoId]: true }));
      try {
        await api.processVideo(videoId);
        toastSuccess("Generating new reels! Redirecting…");
        setTimeout(() => {
          router.push(`/upload/status?videoId=${videoId}`);
        }, 700);
      } catch (err) {
        const msg =
          err instanceof Error ? err.message : "Could not start generation";
        toastError(msg);
        setGenerating((g) => ({ ...g, [videoId]: false }));
      }
    },
    [router, toastSuccess, toastError],
  );

  if (!authChecked) {
    return (
      <AppShell>
        <div className="min-h-[60vh] flex flex-col items-center justify-center gap-3">
          <Loader2 className="animate-spin text-primary" size={32} />
          <p className="text-gray-400 text-sm">Checking session…</p>
        </div>
      </AppShell>
    );
  }

  return (
    <AppShell>
      <div className="max-w-6xl mx-auto px-4 sm:px-6 py-8">
        {/* --- Section 1: Upload CTA --- */}
        <div className="mb-8 rounded-2xl bg-gradient-to-br from-primary/20 via-secondary/10 to-transparent border border-white/10 p-6 sm:p-8 flex flex-col sm:flex-row items-start sm:items-center gap-4 sm:gap-6">
          <div className="flex-1">
            <h1 className="text-2xl sm:text-3xl font-bold mb-1">
              Your Reels
            </h1>
            <p className="text-sm text-gray-400">
              {videos && videos.length > 0
                ? `${total} video${total === 1 ? "" : "s"} • auto-deletes after 24h`
                : "Upload a video to get started"}
            </p>
          </div>
          <div className="flex items-center gap-2 w-full sm:w-auto">
            <button
              onClick={load}
              disabled={loading}
              className="p-2.5 rounded-full bg-white/5 hover:bg-white/10 text-gray-300 transition-colors disabled:opacity-50"
              aria-label="Refresh"
              title="Refresh"
            >
              <RefreshCw size={16} className={loading ? "animate-spin" : ""} />
            </button>
            <Link
              href="/upload"
              className="flex-1 sm:flex-none bg-gradient-to-r from-primary to-secondary text-white px-6 py-3 rounded-full text-sm font-semibold hover:opacity-90 transition-opacity flex items-center justify-center gap-2"
            >
              <Upload size={16} />
              Upload New Video
            </Link>
          </div>
        </div>

        {videos === null ? (
          // Initial loading
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-6">
            {[0, 1, 2, 3, 4, 5].map((i) => (
              <Skeleton key={i} height={220} className="rounded-2xl" />
            ))}
          </div>
        ) : fetchError && videos.length === 0 ? (
          // Hard error
          <div className="flex flex-col items-center justify-center text-center py-16 px-6 rounded-2xl border border-dashed border-red-500/20 bg-red-500/[0.03]">
            <AlertCircle size={40} className="text-red-400 mb-3" />
            <h3 className="text-lg font-semibold text-white mb-1">Couldn't load videos</h3>
            <p className="text-sm text-gray-400 mb-5">{fetchError}</p>
            <button
              onClick={load}
              className="text-sm bg-white/5 hover:bg-white/10 px-4 py-2 rounded-full"
            >
              Try again
            </button>
          </div>
        ) : videos.length === 0 ? (
          // Empty
          <EmptyState
            icon="📤"
            title="No videos yet"
            body="Upload your first long-form video and we'll turn it into 3-5 viral reels in minutes."
            ctaHref="/upload"
            ctaLabel="Upload Your First Video"
          />
        ) : (
          // Grid
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-6">
            {videos.map((v) => (
              <DashboardCard
                key={v.id}
                video={v}
                isGenerating={!!generating[v.id]}
                onGenerate={() => handleGenerate(v.id)}
              />
            ))}
          </div>
        )}
      </div>
    </AppShell>
  );
}

interface DashboardCardProps {
  video: VideoListItem;
  isGenerating: boolean;
  onGenerate: () => void;
}

function DashboardCard({ video, isGenerating, onGenerate }: DashboardCardProps) {
  const badge = STATUS_BADGE[video.status] ?? {
    label: video.status,
    classes: "bg-white/5 text-gray-300 border-white/10",
  };
  const hasReels = video.reel_count > 0;
  const isProcessing = video.status === "PROCESSING";

  return (
    <div
      className="group rounded-2xl bg-white/[0.03] border border-white/5 hover:border-white/15 hover:bg-white/[0.05] transition-all overflow-hidden"
    >
      {/* 9:16 visual placeholder — clickable to status page */}
      <Link
        href={`/upload/status?videoId=${video.id}`}
        className="block relative aspect-[9/16] max-h-72 bg-gradient-to-br from-primary/15 via-transparent to-secondary/15 flex items-center justify-center"
      >
        <div className="text-5xl opacity-50 group-hover:scale-110 transition-transform">
          🎬
        </div>
        {/* Reel count badge */}
        {hasReels && (
          <div className="absolute top-3 right-3 bg-black/60 backdrop-blur text-white text-xs px-2.5 py-1 rounded-full">
            {video.reel_count} reel{video.reel_count === 1 ? "" : "s"}
          </div>
        )}
        {/* Status badge */}
        <div className="absolute top-3 left-3">
          <span
            className={cn(
              "text-[10px] uppercase tracking-wider font-semibold px-2 py-1 rounded-full border",
              badge.classes,
              badge.pulse && "animate-pulse-stage",
            )}
          >
            {badge.label}
          </span>
        </div>
        {/* In-progress hint */}
        {isProcessing && video.last_stage && (
          <div className="absolute bottom-3 left-3 right-3 text-[11px] text-gray-300 bg-black/60 backdrop-blur px-2.5 py-1 rounded-full truncate">
            {video.last_stage}
          </div>
        )}
      </Link>

      {/* Footer */}
      <div className="p-4 space-y-3">
        <div>
          <p className="text-sm font-medium truncate" title={video.filename}>
            {video.filename}
          </p>
          <div className="flex items-center justify-between mt-1 text-xs text-gray-500">
            <span>{formatCreatedAt(video.created_at)}</span>
            <span>{formatExpiresIn(video.expires_at)}</span>
          </div>
        </div>

        {/* Action buttons */}
        <div className="flex items-center gap-2">
          {hasReels ? (
            <Link
              href={`/upload/gallery?videoId=${video.id}`}
              className="flex-1 flex items-center justify-center gap-1.5 bg-white/5 hover:bg-white/10 text-white text-xs font-medium px-3 py-2 rounded-full transition-colors"
            >
              <Eye size={13} />
              View Reels
            </Link>
          ) : (
            <Link
              href={`/upload/status?videoId=${video.id}`}
              className="flex-1 flex items-center justify-center gap-1.5 bg-white/5 hover:bg-white/10 text-white text-xs font-medium px-3 py-2 rounded-full transition-colors"
            >
              <Eye size={13} />
              View Status
            </Link>
          )}
          <button
            onClick={onGenerate}
            disabled={isGenerating || isProcessing}
            title={isProcessing ? "Already processing" : "Generate fresh reels"}
            className="flex-1 flex items-center justify-center gap-1.5 bg-gradient-to-r from-primary to-secondary text-white text-xs font-semibold px-3 py-2 rounded-full hover:opacity-90 transition-opacity disabled:opacity-60"
          >
            {isGenerating ? (
              <Loader2 size={13} className="animate-spin" />
            ) : (
              <Sparkles size={13} />
            )}
            {isProcessing ? "Generating…" : isGenerating ? "Starting…" : "New Reels"}
          </button>
        </div>
      </div>
    </div>
  );
}
