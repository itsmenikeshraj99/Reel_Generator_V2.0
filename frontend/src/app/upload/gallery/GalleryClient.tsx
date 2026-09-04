"use client";

import { AlertCircle, AlertTriangle, Download, Loader2, Trash2 } from "lucide-react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { useEffect, useState } from "react";

import { api, type Reel } from "@/lib/api";
import { supabase } from "@/lib/supabase";

export default function GalleryClient() {
  const searchParams = useSearchParams();
  const router = useRouter();
  const videoId = searchParams?.get("videoId") ?? null;

  const [reels, setReels] = useState<Reel[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [showDeleteModal, setShowDeleteModal] = useState(false);
  const [isDeleting, setIsDeleting] = useState(false);
  const [info, setInfo] = useState<string | null>(null);
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
          setError(err instanceof Error ? err.message : "Failed to load reels");
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
    setError(null);
    setInfo(null);
    try {
      // Tier-1 instant kill — backend handles everything in one call:
      // deletes the original upload (videos.gcs_uri), every reel's
      // storage object, and the videos row (cascade clears the rest).
      // We previously did this client-side, which leaked the original
      // upload and required the user to be online with the right RLS.
      await api.deleteVideo(videoId);

      setInfo("Session cleared. Redirecting…");
      setTimeout(() => router.push("/"), 1000);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not clear session");
    } finally {
      setIsDeleting(false);
      setShowDeleteModal(false);
    }
  };

  if (!authChecked) {
    return (
      <div className="min-h-screen bg-dark text-white flex flex-col items-center justify-center p-6">
        <Loader2 className="animate-spin text-primary" size={32} />
        <p className="text-gray-400 text-sm mt-3">Checking session…</p>
      </div>
    );
  }

  if (!videoId) {
    return (
      <div className="min-h-screen bg-dark text-white flex flex-col items-center justify-center p-6">
        <AlertCircle size={48} className="text-red-400 mb-4" />
        <h1 className="text-2xl font-bold">Invalid Video ID</h1>
        <Link
          href="/"
          className="mt-6 bg-white text-black px-6 py-3 rounded-full font-bold"
        >
          Go Home
        </Link>
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-dark text-white p-6">
      <div className="max-w-6xl mx-auto">
        <div className="flex justify-between items-center mb-12">
          <div>
            <h1 className="text-4xl font-bold">Your Viral Reels</h1>
            <p className="text-gray-400 mt-2">
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
            className="bg-white/10 hover:bg-red-500/20 text-white border border-white/10 hover:border-red-500/50 px-6 py-3 rounded-full font-medium transition-all flex items-center gap-2"
          >
            <Trash2 size={18} />
            Finish & Clear Session
          </button>
        </div>

        {info && (
          <div
            role="status"
            className="mb-6 p-4 rounded-xl bg-green-500/10 border border-green-500/20 text-green-400 text-sm"
          >
            {info}
          </div>
        )}

        {loading ? (
          <div className="flex flex-col items-center justify-center py-20 space-y-4">
            <Loader2 className="animate-spin text-primary" size={48} />
            <p className="text-gray-400">Loading your reels…</p>
          </div>
        ) : error ? (
          <div className="text-center py-20">
            <AlertCircle size={48} className="mx-auto text-red-400 mb-4" />
            <p className="text-xl text-gray-400">{error}</p>
          </div>
        ) : reels.length === 0 ? (
          <div className="text-center py-20">
            <p className="text-xl text-gray-400">
              No reels yet. Your video may still be processing — head back to the status page.
            </p>
            <Link
              href={`/upload/status?videoId=${videoId}`}
              className="mt-6 inline-block bg-white/5 hover:bg-white/10 border border-white/10 px-6 py-3 rounded-full font-medium transition-all"
            >
              Check Status
            </Link>
          </div>
        ) : (
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-8">
            {reels.map((reel, idx) => (
              <div
                key={reel.id ?? idx}
                className="bg-white/5 border border-white/10 rounded-3xl overflow-hidden group hover:border-primary/50 transition-all"
              >
                <div className="aspect-[9/16] bg-black relative">
                  <video
                    src={reel.url}
                    className="w-full h-full object-cover"
                    controls
                    loop
                    playsInline
                    preload="metadata"
                  />
                </div>
                <div className="p-6 flex justify-between items-center">
                  <div>
                    <h3 className="font-bold text-lg">{reel.title ?? `Reel ${idx + 1}`}</h3>
                    <p className="text-xs text-gray-500">Viral Candidate #{idx + 1}</p>
                  </div>
                  <a
                    href={reel.url}
                    download={`reel-${idx + 1}.mp4`}
                    className="p-3 bg-white text-black rounded-full hover:bg-gray-200 transition-colors"
                    aria-label={`Download reel ${idx + 1}`}
                  >
                    <Download size={20} />
                  </a>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>

      {showDeleteModal && (
        <div
          role="dialog"
          aria-modal="true"
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/80 backdrop-blur-sm p-4"
        >
          <div className="bg-dark border border-white/10 w-full max-w-md rounded-3xl p-8 shadow-2xl">
            <div className="text-center mb-6">
              <div className="inline-flex p-4 rounded-full bg-red-500/10 text-red-500 mb-4">
                <AlertTriangle size={40} />
              </div>
              <h2 className="text-2xl font-bold mb-2">Are you sure?</h2>
              <p className="text-gray-400">
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
    </div>
  );
}
