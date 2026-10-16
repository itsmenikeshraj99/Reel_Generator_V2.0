"use client";

/**
 * UploadDropzone — drag-drop or click-to-pick a video file.
 *
 * The box shows what stage the upload is in, by colour AND icon AND text:
 *   - idle:       grey dashed border, upload icon          (nothing chosen)
 *   - dragOver:   brand-coloured border, tinted bg         (a file is being dragged over)
 *   - ready:      yellow border, file icon, name + size    (chosen, not uploaded yet)
 *   - uploading:  yellow border, pulsing file icon
 *   - uploaded:   green border, tick icon                  (upload finished)
 *   - starting:   green border, tick icon                  (starting the pipeline)
 *   - started:    green border, tick icon
 *   - error:      red border, alert icon                   (message shown under the box)
 *
 * The dropzone itself is a presentation layer — it forwards a File up to the parent via
 * `onFile(file)`; the parent tells it which `phase` it is in and renders validation errors in a
 * sibling slot (not inside the dropzone). Without a `phase` it works out ready/error/idle itself.
 */

import { useCallback, useRef, useState } from "react";
import { AlertCircle, CheckCircle, FileVideo, Upload } from "lucide-react";

import { cn } from "@/lib/cn";
import { PHASE_TONE, TONE, formatBytes, type UploadPhase } from "@/lib/status";

export type { UploadPhase };

interface UploadDropzoneProps {
  /** The currently selected file (or null). */
  file: File | null;
  /** Called with the picked file. */
  onFile: (file: File) => void;
  /** Validation / upload error from the parent (e.g. "File too large"). */
  error?: string | null;
  /** Which stage the upload is in (colours the box). Worked out from file/error if omitted. */
  phase?: UploadPhase;
  /** Overrides the small line under the file name. */
  detail?: string;
  /** Disables all interaction (e.g. during an upload). */
  disabled?: boolean;
  /** Accepted file MIME types. Default: video/*. */
  accept?: string;
  /** Max file size in MB. Default: 500. */
  maxSizeMB?: number;
  /** Optional override text for the idle hint. */
  hint?: string;
}

export function UploadDropzone({
  file,
  onFile,
  error,
  phase,
  detail,
  disabled = false,
  accept = "video/*",
  maxSizeMB = 500,
  hint,
}: UploadDropzoneProps) {
  const inputRef = useRef<HTMLInputElement>(null);
  const [dragOver, setDragOver] = useState(false);

  const validateAndSet = useCallback(
    (picked: File | undefined | null) => {
      if (!picked) return;
      onFile(picked);
    },
    [onFile],
  );

  const handleChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    validateAndSet(e.target.files?.[0]);
  };

  const handleDrop = (e: React.DragEvent<HTMLLabelElement>) => {
    e.preventDefault();
    e.stopPropagation();
    setDragOver(false);
    if (disabled) return;
    validateAndSet(e.dataTransfer.files?.[0]);
  };

  const handleDragOver = (e: React.DragEvent<HTMLLabelElement>) => {
    e.preventDefault();
    e.stopPropagation();
    if (!disabled) setDragOver(true);
  };

  const handleDragLeave = (e: React.DragEvent<HTMLLabelElement>) => {
    e.preventDefault();
    e.stopPropagation();
    setDragOver(false);
  };

  const current: UploadPhase = phase ?? (error ? "error" : file ? "ready" : "idle");
  const tone = TONE[PHASE_TONE[current]];
  const sizeHint = hint ?? `MP4, MOV, AVI (Max ${maxSizeMB}MB)`;
  const size = file ? formatBytes(file.size) : "";

  const subtitle = (() => {
    if (detail) return detail;
    switch (current) {
      case "ready":
        return `${size} • Ready to upload`;
      case "uploading":
        return `${size} • Uploading…`;
      case "uploaded":
        return `${size} • Uploaded ✓`;
      case "starting":
        return "Uploaded ✓ • Starting the pipeline…";
      case "started":
        return "Pipeline started";
      case "error":
        return file ? size : sizeHint;
      default:
        return sizeHint;
    }
  })();

  const Icon =
    current === "uploaded" || current === "starting" || current === "started"
      ? CheckCircle
      : current === "error"
        ? AlertCircle
        : current === "ready" || current === "uploading"
          ? FileVideo
          : Upload;

  const showName = !!file && current !== "idle";

  return (
    <div className="space-y-2">
      <label
        htmlFor="file-input"
        onDrop={handleDrop}
        onDragOver={handleDragOver}
        onDragLeave={handleDragLeave}
        data-phase={current}
        className={cn(
          "relative block border-2 border-dashed rounded-2xl p-10 sm:p-12 text-center transition-all",
          dragOver
            ? "border-primary bg-primary/10 scale-[1.01]"
            : cn(
                tone.border,
                tone.tint,
                current === "idle" && "hover:border-primary/50 hover:bg-black/5",
              ),
          disabled && "pointer-events-none",
          "cursor-pointer",
        )}
      >
        <input
          id="file-input"
          ref={inputRef}
          type="file"
          className="sr-only"
          accept={accept}
          onChange={handleChange}
          disabled={disabled}
        />

        <div className="flex flex-col items-center justify-center space-y-4">
          <div
            className={cn(
              "p-4 rounded-full transition-all",
              dragOver ? "bg-primary/20 text-primary scale-110" : tone.iconWrap,
              current === "uploading" && !dragOver && "animate-pulse-stage",
            )}
          >
            <Icon size={48} className={cn("transition-transform", dragOver && "scale-110")} />
          </div>
          <div className="text-center">
            <p className="text-lg font-medium break-all">
              {dragOver
                ? "Release to upload"
                : showName
                  ? file!.name
                  : "Drag & drop or click to upload"}
            </p>
            <p
              aria-live="polite"
              className={cn(
                "text-sm mt-1",
                dragOver || current === "idle" ? "text-text-subtle" : tone.text,
              )}
            >
              {dragOver ? sizeHint : subtitle}
            </p>
          </div>
        </div>
      </label>

      {error && (
        <div
          role="alert"
          className="flex items-center gap-3 p-3 rounded-xl bg-danger/10 border border-danger/30 text-danger text-sm"
        >
          <AlertCircle size={16} className="shrink-0" />
          {error}
        </div>
      )}
    </div>
  );
}

export default UploadDropzone;
