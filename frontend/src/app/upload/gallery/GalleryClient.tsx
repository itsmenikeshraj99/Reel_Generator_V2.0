"use client";

import { AlertCircle, AlertTriangle, Loader2, Trash2 } from "lucide-react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { useEffect, useState } from "react";

import AppShell from "@/components/AppShell";
import { ReelCard } from "@/components/ReelCard";
import { Skeleton } from "@/components/Skeleton";
import { EmptyState } from "@/components/EmptyState";
import { useToast } from "@/components/Toast";
import { api, type Reel } from "@/lib/api";
import { supabase } from "@/lib/supabase";

export default function GalleryClient() {
  const searchParams = useSearchParams();
  const router = useRouter();
  const { success, error: toastError } = useToast();
  const videoId = searchParams?.get("videoId") ?? null;

  const [reels, setReels] = useState<Reel[]>([]);
  const [loading, setLoading] = useState(true);
  const [fetchError, setFetchError] = useState<string | null>(null);
  const [showDeleteModal, setShowDeleteModal] = useState(false);
  const [isDeleting, setIsDeleting] = useState(false);
  const [authChecked, setAuthChecked] = useState(false);
  const [expiresAt, setExpiresAt] = useState<Date | null>(null);
  const [now, setNow] = useState<Date>(() => new Date());

  // Auth gate
  useEffect(() => {
    let cancelled = false;
    (async () => {
      const {
        data: { user },
      } = await supabase.auth.getUser();
      if (cancelled) return;
      if (!user) {
        router.replace("/?redirect=/upload/gallery");
        return;
      }
      setAuthChecked(true);
    })();
    return () => {
      cancelled = true;
    };
  }, [router]);

  useEffect(() => {
    if (!videoId) return;
    let cancelled = false;
    const load = async () => {
      try {
        const data = await api.getReels(videoId);
        if (!cancelled) setReels(data.reels || []);
      } catch (err) {
        if (!cancelled) {
          setFetchError(err instanceof Error ? err.message : "Failed to load reels");
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    };
    load();
    return () => {
      cancelled = true;
    };
  }, [videoId]);

  // Fetch the video's expires_at so we can show a "X hours left" countdown
  // and let the user understand the urgency. We read the row directly via
  // the anon-key client (RLS restricts to the owner's own rows).
  useEffect(() => {
    if (!videoId) return;
    let cancelled = false;
    (async () => {
      try {
        const { data, error: dbError } = await supabase
          .from("videos")
          .select("expires_at")
          .eq("id", videoId)
          .single();
        if (cancelled) return;
        if (!dbError && data?.expires_at) {
          setExpiresAt(new Date(data.expires_at));
        }
      } catch {
        // RLS may block this from the anon client if the user just
        // logged out — silently ignore and skip the countdown.
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [videoId]);

  // Re-render once a minute so the countdown updates without a full refresh.
  useEffect(() => {
    const id = setInterval(() => setNow(new Date()), 60_000);
    return () => clearInterval(id);
  }, []);

  const handleFinishSession = async () => {
    if (!videoId) return;
    setIsDeleting(true);
    try {
      // Tier-1 instant kill — backend handles everything in one call:
      // deletes the original upload (videos.gcs_uri), every reel's
      // storage object, and the videos row (cascade clears the rest).
      await api.deleteVideo(videoId);

      // Replace the old in-page green banner with a toast + dashboard
      // bounce. Toast confirms the action; bounce is faster than the
      // old 1-second "Redirecting…" delay.
      success("Session cleared ✓");
      setShowDeleteModal(false);
      router.replace("/dashboard");
    } catch (err) {
      const msg = err instanceof Error ? err.message : "Could not clear session";
      toastError(msg);
    } finally {
      setIsDeleting(false);
    }
  };

  if (!authChecked) {
    return (
      <AppShell>
        <div className="min-h-[60vh] flex flex-col items-center justify-center p-6">
          <Loader2 className="animate-spin text-primary" size={32} />
          <p className="text-gray-400 text-sm mt-3">Checking session…</p>
        </div>
      </AppShell>
    );
  }

  if (!videoId) {
    return (
      <AppShell>
        <div className="min-h-[60vh] flex flex-col items-center justify-center p-6">
          <AlertCircle size={48} className="text-red-400 mb-4" />
          <h1 className="text-2xl font-bold">Invalid Video ID</h1>
          <Link
            href="/dashboard"
            className="mt-6 bg-white text-black px-6 py-3 rounded-full font-bold"
          >
            Go to Dashboard
          </Link>
        </div>
      </AppShell>
    );
  }

  return (
    <AppShell>
      <div className="max-w-6xl mx-auto px-4 sm:px-6 py-8">
        <div className="flex justify-between items-start gap-4 mb-10 flex-wrap">
          <div>
            <h1 className="text-2xl sm:text-4xl font-bold">Your Viral Reels</h1>
            <p className="text-gray-400 mt-2 text-sm">
              Download your clips and share them with the world!
            </p>
            {expiresAt && (() => {
              const msLeft = expiresAt.getTime() - now.getTime();
              if (msLeft <= 0) {
                return (
                  <p className="text-red-400 text-sm mt-1">
                    ⏰ Session expired — files will be deleted on the next server sweep.
                  </p>
                );
              }
              const hoursLeft = Math.floor(msLeft / 3_600_000);
              const minutesLeft = Math.floor((msLeft % 3_600_000) / 60_000);
              return (
                <p className="text-amber-400/80 text-sm mt-1">
                  ⏰ {hoursLeft}h {minutesLeft}m until auto-delete
                </p>
              );
            })()}
          </div>
          <button
            onClick={() => setShowDeleteModal(true)}
            className="bg-white/10 hover:bg-red-500/20 text-white border border-white/10 hover:border-red-500/50 px-5 py-2.5 rounded-full text-sm font-medium transition-all flex items-center gap-2"
          >
            <Trash2 size={16} />
            Finish &amp; Clear Session
          </button>
        </div>

        {loading ? (
          <GallerySkeleton />
        ) : fetchError ? (
          <div className="text-center py-20">
            <AlertCircle size={48} className="mx-auto text-red-400 mb-4" />
            <p className="text-xl text-gray-400 mb-6">{fetchError}</p>
            <Link
              href="/dashboard"
              className="inline-block bg-white/5 hover:bg-white/10 border border-white/10 px-6 py-3 rounded-full font-medium transition-all"
            >
              Back to Dashboard
            </Link>
          </div>
        ) : reels.length === 0 ? (
          <EmptyState
            icon="🎞️"
            title="No reels yet"
            body="Your video may still be processing — head back to the status page."
            ctaHref={`/upload/status?videoId=${videoId}`}
            ctaLabel="Check Status"
          />
        ) : (
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6 sm:gap-8">
            {reels.map((reel, idx) => (
              <ReelCard
                key={reel.id ?? `${idx}-${reel.storage_path ?? "reel"}`}
                reel={reel}
                index={idx}
              />
            ))}
          </div>
        )}
      </div>

      {showDeleteModal && (
        // Modal z-index 40: below toasts (z-100), so a "Link copied!" toast
        // remains visible above this confirm. Matches PR 3's adjustment.
        <div
          role="dialog"
          aria-modal="true"
          className="fixed inset-0 z-40 flex items-center justify-center bg-black/80 backdrop-blur-sm p-4 animate-toast-in"
        >
          <div className="bg-dark border border-white/10 w-full max-w-md rounded-3xl p-8 shadow-2xl">
            <div className="text-center mb-6">
              <div className="inline-flex p-4 rounded-full bg-red-500/10 text-red-500 mb-4">
                <AlertTriangle size={40} />
              </div>
              <h2 className="text-2xl font-bold mb-2">Are you sure?</h2>
              <p className="text-gray-400 text-sm">
                Please download and share your reels right now! To protect your privacy, your
                original video and all generated reels will be permanently deleted.
              </p>
            </div>

            <div className="flex gap-4">
              <button
                onClick={() => setShowDeleteModal(false)}
                disabled={isDeleting}
                className="flex-1 py-3 rounded-xl bg-white/5 hover:bg-white/10 border border-white/10 font-medium transition-all disabled:opacity-50"
              >
                Cancel
              </button>
              <button
                onClick={handleFinishSession}
                disabled={isDeleting}
                className="flex-1 py-3 rounded-xl bg-red-500 hover:bg-red-600 text-white font-bold transition-all disabled:opacity-50"
              >
                {isDeleting ? "Deleting…" : "Yes, Clear Everything"}
              </button>
            </div>
          </div>
        </div>
      )}
    </AppShell>
  );
}

function GallerySkeleton() {
  return (
    <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6 sm:gap-8">
      {[0, 1, 2, 3, 4, 5].map((i) => (
        <div key={i} className="bg-white/5 border border-white/10 rounded-3xl overflow-hidden">
          <Skeleton height={0} className="aspect-[9/16] w-full" />
          <div className="p-4">
            <Skeleton height={14} width="60%" />
            <Skeleton height={10} width="40%" className="mt-2" />
          </div>
        </div>
      ))}
    </div>
  );
}
