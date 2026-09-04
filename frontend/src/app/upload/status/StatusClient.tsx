"use client";

import { AlertCircle, CheckCircle, Clock, Loader2, RefreshCw } from "lucide-react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { useCallback, useEffect, useRef, useState } from "react";

import AppShell from "@/components/AppShell";
import { useToast } from "@/components/Toast";
import { api, type StatusResponse } from "@/lib/api";
import { supabase } from "@/lib/supabase";

type StageState = "done" | "active" | "pending" | "failed";

interface StageConfig {
  key: string;
  label: string;
  icon: string;
  estimatedSeconds: number;
}

const STAGE_CONFIG: StageConfig[] = [
  { key: "VALIDATING", label: "Validating Video", icon: "🎬", estimatedSeconds: 5 },
  { key: "TRANSCRIBING_PLANNING", label: "AI Analysis & Planning", icon: "🧠", estimatedSeconds: 60 },
  { key: "REVIEWING", label: "Reviewing Quality", icon: "🔍", estimatedSeconds: 30 },
  { key: "RENDERING", label: "Rendering Final Reels", icon: "🎥", estimatedSeconds: 90 },
];

const TOTAL_ESTIMATED_SECONDS = STAGE_CONFIG.reduce(
  (acc, s) => acc + s.estimatedSeconds,
  0,
);

function classifyStages(
  currentStatus: string,
  failed: boolean,
): Record<string, StageState> {
  if (currentStatus === "READY") {
    return Object.fromEntries(STAGE_CONFIG.map((s) => [s.key, "done" as StageState]));
  }
  if (failed || currentStatus === "FAILED") {
    // Mark every stage up to the failed one as "done" so the user can see
    // how far the pipeline got before failing.
    const idx = STAGE_CONFIG.findIndex((s) => s.key === currentStatus);
    return Object.fromEntries(
      STAGE_CONFIG.map((s, i) => {
        if (idx === -1) return [s.key, "pending"];
        if (i < idx) return [s.key, "done"];
        if (i === idx) return [s.key, "failed"];
        return [s.key, "pending"];
      }),
    );
  }
  const idx = STAGE_CONFIG.findIndex((s) => s.key === currentStatus);
  if (idx === -1) {
    return Object.fromEntries(STAGE_CONFIG.map((s) => [s.key, "pending" as StageState]));
  }
  return Object.fromEntries(
    STAGE_CONFIG.map((s, i) => {
      const state: StageState = i < idx ? "done" : i === idx ? "active" : "pending";
      return [s.key, state];
    }),
  );
}

function formatRemaining(seconds: number): string {
  if (seconds <= 0) return "almost done";
  if (seconds < 60) return `~${Math.ceil(seconds)}s remaining`;
  return `~${Math.ceil(seconds / 60)} min remaining`;
}

export default function StatusClient() {
  const searchParams = useSearchParams();
  const router = useRouter();
  const { success } = useToast();
  const videoId = searchParams?.get("videoId") ?? null;

  const [status, setStatus] = useState<string>("UNKNOWN");
  const [error, setError] = useState<string | null>(null);
  const [authChecked, setAuthChecked] = useState(false);
  const [pollCount, setPollCount] = useState(0);
  const [lastUpdated, setLastUpdated] = useState<Date | null>(null);
  const startedAtRef = useRef<number>(Date.now());
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);
  // Guard so the READY toast fires once per session, not on every poll
  const readyFiredRef = useRef<boolean>(false);

  // Auth gate
  useEffect(() => {
    let cancelled = false;
    (async () => {
      const {
        data: { user },
      } = await supabase.auth.getUser();
      if (cancelled) return;
      if (!user) {
        router.replace("/?redirect=/upload/status");
        return;
      }
      setAuthChecked(true);
    })();
    return () => {
      cancelled = true;
    };
  }, [router]);

  const poll = useCallback(async () => {
    if (!videoId) return;
    setPollCount((c) => c + 1);
    try {
      const data: StatusResponse = await api.getVideoStatus(videoId);
      const currentStatus = data.status || "UNKNOWN";
      setStatus(currentStatus);
      setLastUpdated(new Date());
      setError(null);

      if (currentStatus === "READY") {
        if (!readyFiredRef.current) {
          readyFiredRef.current = true;
          success("Your reels are ready! 🎬");
        }
        if (pollRef.current) {
          clearInterval(pollRef.current);
          pollRef.current = null;
        }
      } else if (currentStatus === "FAILED") {
        setError(data.error || "Processing failed. Please try again.");
        if (pollRef.current) {
          clearInterval(pollRef.current);
          pollRef.current = null;
        }
      }
    } catch (err) {
      // Tier-3 failsafe: backend returns 403 once the 24h window passes.
      // We stop polling, surface a clear "session ended" message, and
      // give the user a path back to the home page.
      const msg = err instanceof Error ? err.message : "Failed to fetch status";
      if (msg.toLowerCase().includes("expired") || msg.toLowerCase().includes("session ended")) {
        setStatus("EXPIRED");
        setError("This session has ended. Your video and reels were auto-deleted after 24 hours.");
        if (pollRef.current) {
          clearInterval(pollRef.current);
          pollRef.current = null;
        }
        return;
      }
      // Don't stop polling on transient errors; just record the last one.
      setError(msg);
    }
  }, [videoId, success]);

  useEffect(() => {
    if (!videoId) {
      setError("No video ID provided");
      return;
    }
    startedAtRef.current = Date.now();
    setStatus("UNKNOWN");
    readyFiredRef.current = false;

    // First call immediately, then every 2s for snappier UX.
    poll();
    pollRef.current = setInterval(poll, 2000);

    return () => {
      if (pollRef.current) {
        clearInterval(pollRef.current);
        pollRef.current = null;
      }
    };
  }, [videoId, poll]);

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

  const stageStates = classifyStages(status, status === "FAILED");
  const completedCount = Object.values(stageStates).filter((s) => s === "done").length;
  const baseProgress = (completedCount / STAGE_CONFIG.length) * 100;
  const isReady = status === "READY";
  const isFailed = status === "FAILED";

  // Smooth progress: while a stage is active, animate from base% to base%+25%
  // so the bar feels alive even before the backend advances the stage.
  const activeBonus = Object.values(stageStates).includes("active") ? 25 : 0;
  const progressPct = Math.min(100, Math.round(baseProgress + activeBonus));

  // Remaining time estimate based on how long each stage is taking on average.
  const elapsedTotal = (Date.now() - startedAtRef.current) / 1000;
  let remainingSeconds = 0;
  if (!isReady && !isFailed) {
    const activeIdx = STAGE_CONFIG.findIndex(
      (s) => stageStates[s.key] === "active",
    );
    if (activeIdx >= 0) {
      const stagesBeforeElapsed = STAGE_CONFIG.slice(0, activeIdx).reduce(
        (acc, s) => acc + s.estimatedSeconds,
        0,
      );
      const elapsedOnStage = Math.max(0, elapsedTotal - stagesBeforeElapsed);
      const stageRemaining = Math.max(
        0,
        STAGE_CONFIG[activeIdx].estimatedSeconds - elapsedOnStage,
      );
      remainingSeconds =
        stageRemaining +
        STAGE_CONFIG.slice(activeIdx + 1).reduce(
          (acc, s) => acc + s.estimatedSeconds,
          0,
        );
    } else {
      remainingSeconds = TOTAL_ESTIMATED_SECONDS;
    }
  }

  return (
    <AppShell>
      <div className="min-h-[80vh] flex flex-col items-center justify-center p-6">
        <div className="w-full max-w-lg bg-white/5 border border-white/10 rounded-3xl p-10 backdrop-blur-md shadow-2xl">
          <div className="text-center mb-8">
            <div className="inline-flex p-4 rounded-full bg-white/5 mb-4">
              {isReady ? (
                <CheckCircle size={48} className="text-green-400" />
              ) : isFailed ? (
                <AlertCircle size={48} className="text-red-400" />
              ) : (
                <Loader2 className="animate-spin text-primary" size={48} />
              )}
            </div>
            <h1 className="text-3xl font-bold mb-2">
              {isReady
                ? "Reels Ready!"
                : isFailed
                  ? "Processing Failed"
                  : "Processing Your Video"}
            </h1>
            <p className="text-gray-400 text-sm">
              {isReady
                ? "Your viral clips have been generated"
                : isFailed
                  ? "Something went wrong — see error below"
                  : formatRemaining(remainingSeconds)}
            </p>
          </div>

          {/* Overall progress bar */}
          {!isFailed && (
            <div className="mb-8">
              <div className="flex justify-between text-xs text-gray-400 mb-1">
                <span>
                  {completedCount} of {STAGE_CONFIG.length} steps complete
                </span>
                <span>{progressPct}%</span>
              </div>
              <div className="h-2 w-full bg-white/10 rounded-full overflow-hidden">
                <div
                  className={`h-full transition-all duration-700 ease-out ${
                    isReady
                      ? "bg-green-500"
                      : "bg-gradient-to-r from-primary to-secondary"
                  }`}
                  style={{ width: `${progressPct}%` }}
                />
              </div>
            </div>
          )}

          {/* Stage list */}
          <div className="space-y-4 text-left">
            {STAGE_CONFIG.map((stage) => {
              const state: StageState = stageStates[stage.key] ?? "pending";
              return <StageRow key={stage.key} stage={stage} state={state} />;
            })}
          </div>

          {/* Error banner (also shown for transient poll errors) */}
          {error && (isFailed || status === "EXPIRED") && (
            <div
              role="alert"
              className="mt-8 p-4 rounded-xl bg-red-500/10 border border-red-500/20 text-red-400 text-sm flex items-start gap-2"
            >
              <AlertCircle size={16} className="mt-0.5 flex-shrink-0" />
              <span>{error}</span>
            </div>
          )}

          {/* Diagnostics footer */}
          <div className="mt-6 flex items-center justify-between text-xs text-gray-500">
            <div className="flex items-center gap-1.5">
              <RefreshCw size={10} />
              <span>
                {lastUpdated
                  ? `Updated ${lastUpdated.toLocaleTimeString()}`
                  : "Connecting…"}
              </span>
            </div>
            <span>polls: {pollCount}</span>
          </div>

          {isReady && (
            <div className="mt-10 space-y-3">
              <button
                onClick={() => router.push(`/upload/gallery?videoId=${videoId}`)}
                className="w-full bg-gradient-to-r from-primary to-secondary text-white py-4 rounded-full font-bold text-lg hover:opacity-90 transition-all"
              >
                View My Reels 🎬
              </button>
              <Link
                href="/dashboard"
                className="block w-full bg-white/5 hover:bg-white/10 border border-white/10 text-white py-3 rounded-full font-medium text-center text-sm transition-all"
              >
                Open Dashboard
              </Link>
            </div>
          )}

          {isFailed && (
            <Link
              href="/dashboard"
              className="mt-10 inline-block w-full bg-white/5 hover:bg-white/10 border border-white/10 text-white py-4 rounded-full font-bold text-lg text-center transition-all"
            >
              Back to Dashboard
            </Link>
          )}

          {status === "EXPIRED" && (
            <Link
              href="/dashboard"
              className="mt-10 inline-block w-full bg-white/5 hover:bg-white/10 border border-white/10 text-white py-4 rounded-full font-bold text-lg text-center transition-all"
            >
              Back to Dashboard
            </Link>
          )}
        </div>
      </div>
    </AppShell>
  );
}

function StageRow({ stage, state }: { stage: StageConfig; state: StageState }) {
  const isDone = state === "done";
  const isActive = state === "active";
  const isFailed = state === "failed";

  return (
    <div
      className={`flex items-center gap-4 p-3 rounded-xl transition-all duration-500 ${
        isActive
          ? "bg-white/5 scale-[1.02] border border-white/10"
          : isDone
            ? "bg-green-500/5 border border-green-500/20"
            : isFailed
              ? "bg-red-500/5 border border-red-500/20"
              : "opacity-40"
      }`}
    >
      <div
        className={`w-9 h-9 rounded-full flex items-center justify-center text-sm flex-shrink-0 ${
          isDone
            ? "bg-green-500 text-white"
            : isActive
              ? "bg-white/10 text-current"
              : isFailed
                ? "bg-red-500/20 text-red-400"
                : "bg-white/5"
        }`}
      >
        {isDone ? (
          <CheckCircle size={18} />
        ) : isActive ? (
          <Loader2 className="animate-spin" size={18} />
        ) : isFailed ? (
          <AlertCircle size={18} />
        ) : (
          <span className="text-base">{stage.icon}</span>
        )}
      </div>
      <div className="flex-1 min-w-0">
        <p
          className={`font-medium text-sm ${
            isDone
              ? "text-green-400"
              : isActive
                ? "text-white"
                : isFailed
                  ? "text-red-400"
                  : "text-gray-400"
          }`}
        >
          {stage.label}
        </p>
        <p className="text-xs text-gray-500 mt-0.5">
          {isDone ? (
            "Done"
          ) : isActive ? (
            <span className="flex items-center gap-1">
              <Clock size={10} /> ~{stage.estimatedSeconds}s • in progress
            </span>
          ) : isFailed ? (
            "Failed"
          ) : (
            "Waiting…"
          )}
        </p>
      </div>
      {isActive && (
        <div className="h-1.5 w-16 bg-white/10 rounded-full overflow-hidden flex-shrink-0">
          <div className="h-full bg-current animate-pulse w-full" />
        </div>
      )}
    </div>
  );
}
