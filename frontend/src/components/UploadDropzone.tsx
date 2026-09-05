"use client";

/**
 * UploadDropzone — drag-drop or click-to-pick a video file.
 *
 * Visual states:
 *   - idle:        dashed white border, dim icon
 *   - dragOver:    primary-coloured border, tinted bg, scale-up icon
 *   - selected:    primary-coloured border, shows the filename
 *   - disabled:    pointer-events-none + opacity-70
 *
 * The dropzone itself is a presentation layer — it forwards a File
 * up to the parent via `onFile(file)` and renders validation errors
 * in a sibling slot (not inside the dropzone).
 */

import { useCallback, useRef, useState } from "react";
import { FileVideo, Upload, AlertCircle } from "lucide-react";

import { cn } from "@/lib/cn";

interface UploadDropzoneProps {
  /** The currently selected file (or null). */
  file: File | null;
  /** Called with the picked file. */
  onFile: (file: File) => void;
  /** Validation error from the parent (e.g. "File too large"). */
  error?: string | null;
  /** Disables all interaction (e.g. during an upload). */
  disabled?: boolean;
  /** Accepted file MIME types. Default: video/*. */
  accept?: string;
  /** Max file size in MB. Default: 500. */
  maxSizeMB?: number;
  /** Optional override text. */
  hint?: string;
}

export function UploadDropzone({
  file,
  onFile,
  error,
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

  const showSelected = !!file;
  const sizeHint = hint ?? `MP4, MOV, AVI (Max ${maxSizeMB}MB)`;

  return (
    <div className="space-y-2">
      <label
        htmlFor="file-input"
        onDrop={handleDrop}
        onDragOver={handleDragOver}
        onDragLeave={handleDragLeave}
        className={cn(
          "relative block border-2 border-dashed rounded-2xl p-10 sm:p-12 text-center transition-all",
          // Idle
          !showSelected && !dragOver && !error && "border-border hover:border-primary/50 hover:bg-black/5",
          // Drag-over visual
          dragOver && "border-primary bg-primary/10 scale-[1.01]",
          // Selected visual
          showSelected && !error && "border-primary bg-primary/5",
          // Error visual
          error && "border-red-500/40 bg-red-500/[0.03]",
          // Disabled
          disabled && "pointer-events-none opacity-70",
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
              dragOver
                ? "bg-primary/20 scale-110"
                : "bg-black/5 text-primary",
            )}
          >
            {showSelected ? (
              <FileVideo size={48} className="text-primary" />
            ) : (
              <Upload
                size={48}
                className={cn(
                  "transition-transform",
                  dragOver && "scale-110",
                )}
              />
            )}
          </div>
          <div className="text-center">
            <p className="text-lg font-medium">
              {dragOver
                ? "Release to upload"
                : file
                ? file.name
                : "Drag & drop or click to upload"}
            </p>
            <p className="text-sm text-text-subtle mt-1">{sizeHint}</p>
          </div>
        </div>
      </label>

      {error && (
        <div
          role="alert"
          className="flex items-center gap-3 p-3 rounded-xl bg-red-500/10 border border-red-500/20 text-red-400 text-sm"
        >
          <AlertCircle size={16} className="shrink-0" />
          {error}
        </div>
      )}
    </div>
  );
}

export default UploadDropzone;
