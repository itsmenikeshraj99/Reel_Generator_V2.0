"use client";

/**
 * Upload page — Phase 11 refactor.
 *
 * Behaviour:
 *   1. Pick a video via <UploadDropzone>.
 *   2. Click "Upload Video": backend returns a signed upload URL →
 *      XHR PUT the file with progress reporting.
 *   3. Click "Generate AI Reels ✨": backend kicks off the pipeline →
 *      redirect to /upload/status?videoId=<id>.
 *
 * Auth: same client-side gate as before. The backend also enforces
 * auth (Depends(get_current_user)), so a bypassed gate would just
 * return 401.
 *
 * Fallback: if the backend doesn't return a signed upload URL
 * (e.g. pre-PR1 deploy), fall back to supabase.storage.upload().
 */

import { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { CheckCircle, Loader2 } from "lucide-react";

import AppShell from "@/components/AppShell";
import { ProgressBar } from "@/components/ProgressBar";
import { UploadDropzone } from "@/components/UploadDropzone";
import { Skeleton } from "@/components/Skeleton";
import { useToast } from "@/components/Toast";
import { api, uploadFileToSignedUrl } from "@/lib/api";
import { supabase } from "@/lib/supabase";

const MAX_SIZE_MB = 500;
const ALLOWED_MIME_PREFIX = "video/";

type UploadStatus =
  | "idle"
  | "uploading"
  | "processed"
  | "processing"
  | "success"
  | "error";

export default function UploadPage() {
  const router = useRouter();
  const { success: toastSuccess, error: toastError } = useToast();

  const [authChecked, setAuthChecked] = useState(false);
  const [file, setFile] = useState<File | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [status, setStatus] = useState<UploadStatus>("idle");
  const [progress, setProgress] = useState(0);
  const [videoId, setVideoId] = useState<string | null>(null);
  const inputRef = useRef<HTMLInputElement | null>(null);

  // Client-side auth gate.
  useEffect(() => {
    let cancelled = false;
    (async () => {
      const { data: { user } } = await supabase.auth.getUser();
      if (cancelled) return;
      if (!user) {
        router.replace("/?redirect=/upload");
        return;
      }
      setAuthChecked(true);
    })();
    return () => {
      cancelled = true;
    };
  }, [router]);

  const reset = (clearInput = true) => {
    setFile(null);
    setVideoId(null);
    setStatus("idle");
    setError(null);
    setProgress(0);
    if (clearInput && inputRef.current) inputRef.current.value = "";
  };

  const handleFilePicked = (picked: File) => {
    if (!picked.type.startsWith(ALLOWED_MIME_PREFIX)) {
      setError("Please select a video file.");
      reset();
      return;
    }
    if (picked.size > MAX_SIZE_MB * 1024 * 1024) {
      setError(`File size exceeds ${MAX_SIZE_MB}MB limit.`);
      reset();
      return;
    }
    setFile(picked);
    setError(null);
    setStatus("idle");
    setVideoId(null);
  };

  const handleUpload = async () => {
    if (!file) return;
    setStatus("uploading");
    setError(null);
    setProgress(0);

    try {
      const { data: { user } } = await supabase.auth.getUser();
      if (!user) throw new Error("You must be logged in to upload.");

      const uploadInfo = await api.uploadUrl(file.name);
      setVideoId(uploadInfo.video_id);

      // PR 11 path: use the signed upload URL for progress reporting.
      if (uploadInfo.signed_upload_url) {
        await uploadFileToSignedUrl(
          uploadInfo.signed_upload_url,
          file,
          file.type || "video/mp4",
          (p) => setProgress(p.pct),
        );
      } else {
        // Fallback for pre-PR1 backends: no progress, but still works.
        const { error: uploadError } = await supabase.storage
          .from(uploadInfo.bucket)
          .upload(uploadInfo.storage_path, file, {
            upsert: true,
            contentType: file.type,
          });
        if (uploadError) throw uploadError;
      }

      setStatus("processed");
      setProgress(100);
    } catch (err) {
      const msg = err instanceof Error ? err.message : "Upload failed.";
      setError(msg);
      toastError(msg);
      setStatus("error");
    }
  };

  const handleProcess = async () => {
    if (!videoId) return;
    setStatus("processing");
    setError(null);

    try {
      await api.processVideo(videoId);
      setStatus("success");
      toastSuccess("Processing started! Redirecting…");
      setTimeout(() => {
        router.push(`/upload/status?videoId=${videoId}`);
      }, 1200);
    } catch (err) {
      const msg =
        err instanceof Error ? err.message : "Failed to start processing.";
      setError(msg);
      toastError(msg);
      setStatus("error");
    }
  };

  const isUploading = status === "uploading";
  const isProcessing = status === "processing";
  const isBusy = isUploading || isProcessing;
  const isProcessed = status === "processed";
  const isSuccess = status === "success";

  const buttonLabel = (() => {
    if (isUploading) return `Uploading… ${progress}%`;
    if (isProcessing) return "Starting pipeline…";
    if (isProcessed) return "Generate AI Reels ✨";
    return "Upload Video 📤";
  })();

  const buttonAction = isProcessed ? handleProcess : handleUpload;

  if (!authChecked) {
    return (
      <AppShell>
        <div className="min-h-[60vh] flex flex-col items-center justify-center p-6 gap-3">
          <Skeleton width={120} height={40} className="rounded-full" />
        </div>
      </AppShell>
    );
  }

  return (
    <AppShell>
      <div className="max-w-2xl mx-auto px-4 sm:px-6 py-8 sm:py-12">
        <div className="text-center mb-8">
          <h1 className="text-3xl sm:text-4xl font-bold mb-2">Upload Video</h1>
          <p className="text-gray-400">
            Upload your long-form video to generate viral reels
          </p>
        </div>

        <div className="bg-white/5 border border-white/10 rounded-3xl p-6 sm:p-8 backdrop-blur-md shadow-2xl">
          {/* Hidden native input used by the dropzone's label */}
          <input
            ref={inputRef}
            type="file"
            accept="video/*"
            className="sr-only"
            onChange={(e) => handleFilePicked(e.target.files?.[0] as File)}
          />
          <UploadDropzone
            file={file}
            onFile={handleFilePicked}
            error={error && !isUploading && !isProcessing ? error : null}
            disabled={isBusy}
            maxSizeMB={MAX_SIZE_MB}
          />

          {/* Progress bar — only shown during upload */}
          {isUploading && (
            <div className="mt-6">
              <ProgressBar value={progress} label="Uploading" />
            </div>
          )}

          {/* Indeterminate bar while backend kicks off the pipeline */}
          {isProcessing && (
            <div className="mt-6">
              <ProgressBar indeterminate label="Starting pipeline" />
            </div>
          )}

          {isSuccess && (
            <div
              role="status"
              className="mt-6 flex items-center justify-center gap-3 p-4 rounded-xl bg-green-500/10 border border-green-500/20 text-green-400 text-sm"
            >
              <CheckCircle size={18} />
              Pipeline started! Redirecting to status page…
            </div>
          )}

          <div className="flex justify-center mt-6">
            {file && !isSuccess && (
              <button
                onClick={buttonAction}
                disabled={isBusy}
                className="bg-gradient-to-r from-primary to-secondary text-white px-8 sm:px-10 py-3.5 sm:py-4 rounded-full font-bold text-base sm:text-lg hover:opacity-90 transition-all disabled:opacity-50 flex items-center gap-2"
              >
                {(isBusy) && <Loader2 className="animate-spin" size={20} />}
                {buttonLabel}
              </button>
            )}
          </div>
        </div>

        <p className="text-center text-xs text-gray-500 mt-6">
          Your video is auto-deleted after 24 hours • Max {MAX_SIZE_MB}MB
        </p>
      </div>
    </AppShell>
  );
}
