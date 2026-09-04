"use client";

import {
  AlertCircle,
  CheckCircle,
  FileVideo,
  Loader2,
  Upload,
} from "lucide-react";
import { useRouter } from "next/navigation";
import { useEffect, useRef, useState } from "react";

import { api } from "@/lib/api";
import { supabase } from "@/lib/supabase";

const MAX_SIZE_MB = 500;
const ALLOWED_MIME_PREFIX = "video/";

export default function UploadPage() {
  const [authChecked, setAuthChecked] = useState(false);
  const [file, setFile] = useState<File | null>(null);
  const [uploading, setUploading] = useState(false);
  const [status, setStatus] = useState<
    "idle" | "uploading" | "processed" | "processing" | "success" | "error"
  >("idle");
  const [videoId, setVideoId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const router = useRouter();
  const inputRef = useRef<HTMLInputElement>(null);

  // Client-side auth gate. The backend also enforces auth (Depends(get_current_user))
  // so even if this is bypassed, every API call returns 401.
  useEffect(() => {
    let cancelled = false;
    (async () => {
      const {
        data: { user },
      } = await supabase.auth.getUser();
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

  const reset = () => {
    setFile(null);
    setVideoId(null);
    setStatus("idle");
    setError(null);
    if (inputRef.current) inputRef.current.value = "";
  };

  const handleFileChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const selected = e.target.files?.[0];
    if (!selected) return;

    if (!selected.type.startsWith(ALLOWED_MIME_PREFIX)) {
      setError("Please select a video file.");
      reset();
      return;
    }
    if (selected.size > MAX_SIZE_MB * 1024 * 1024) {
      setError(`File size exceeds ${MAX_SIZE_MB}MB limit.`);
      reset();
      return;
    }

    setFile(selected);
    setError(null);
    setStatus("idle");
    setVideoId(null);
  };

  const handleUpload = async () => {
    if (!file) return;
    setUploading(true);
    setStatus("uploading");
    setError(null);

    try {
      const {
        data: { user },
      } = await supabase.auth.getUser();
      if (!user) throw new Error("You must be logged in to upload.");

      const { video_id, storage_path, bucket } = await api.uploadUrl(file.name);
      setVideoId(video_id);

      const { error: uploadError } = await supabase.storage
        .from(bucket)
        .upload(storage_path, file, { upsert: true, contentType: file.type });
      if (uploadError) throw uploadError;

      setStatus("processed");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Upload failed.");
      setStatus("error");
    } finally {
      setUploading(false);
    }
  };

  const handleProcess = async () => {
    if (!videoId) return;
    setUploading(true);
    setStatus("processing");
    setError(null);

    try {
      await api.processVideo(videoId);
      setStatus("success");
      setTimeout(() => {
        router.push(`/upload/status?videoId=${videoId}`);
      }, 1200);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to start processing.");
      setStatus("error");
    } finally {
      setUploading(false);
    }
  };

  const buttonLabel = (() => {
    if (uploading) {
      return status === "uploading" ? "Uploading…" : "Starting pipeline…";
    }
    if (status === "processed") return "Generate AI Reels ✨";
    return "Upload Video 📤";
  })();

  return (
    <div className="min-h-screen bg-dark text-white flex flex-col items-center justify-center p-6">
      {!authChecked && (
        <div className="flex flex-col items-center gap-3">
          <Loader2 className="animate-spin text-primary" size={32} />
          <p className="text-gray-400 text-sm">Checking session…</p>
        </div>
      )}
      <div className="w-full max-w-2xl bg-white/5 border border-white/10 rounded-3xl p-8 backdrop-blur-md shadow-2xl">
        <div className="text-center mb-10">
          <h1 className="text-4xl font-bold mb-2">Upload Video</h1>
          <p className="text-gray-400">
            Upload your long-form video to generate viral reels
          </p>
        </div>

        <label
          htmlFor="file-input"
          className={`relative block border-2 border-dashed rounded-2xl p-12 text-center transition-all cursor-pointer
            ${file ? "border-primary bg-primary/5" : "border-white/20 hover:border-primary/50 hover:bg-white/5"}
            ${status === "uploading" ? "pointer-events-none opacity-70" : ""}`}
        >
          <input
            id="file-input"
            ref={inputRef}
            type="file"
            className="sr-only"
            accept="video/*"
            onChange={handleFileChange}
          />

          <div className="flex flex-col items-center justify-center space-y-4">
            <div className="p-4 bg-white/5 rounded-full text-primary">
              {file ? <FileVideo size={48} /> : <Upload size={48} />}
            </div>
            <div className="text-center">
              <p className="text-lg font-medium">
                {file ? file.name : "Drag & drop or click to upload"}
              </p>
              <p className="text-sm text-gray-500 mt-1">
                MP4, MOV, AVI (Max {MAX_SIZE_MB}MB)
              </p>
            </div>
          </div>
        </label>

        <div className="mt-8 space-y-6">
          {error && (
            <div
              role="alert"
              className="flex items-center gap-3 p-4 rounded-xl bg-red-500/10 border border-red-500/20 text-red-400 text-sm"
            >
              <AlertCircle size={18} />
              {error}
            </div>
          )}

          {status === "success" && (
            <div
              role="status"
              className="flex items-center justify-center gap-3 p-4 rounded-xl bg-green-500/10 border border-green-500/20 text-green-400 text-sm"
            >
              <CheckCircle size={18} />
              Upload successful! Redirecting to status page…
            </div>
          )}

          <div className="flex justify-center">
            {file && status !== "success" && (
              <button
                onClick={status === "processed" ? handleProcess : handleUpload}
                disabled={uploading}
                className="bg-gradient-to-r from-primary to-secondary text-white px-10 py-4 rounded-full font-bold text-lg hover:opacity-90 transition-all disabled:opacity-50 flex items-center gap-2"
              >
                {uploading && <Loader2 className="animate-spin" size={20} />}
                {buttonLabel}
              </button>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
