/**
 * Shared status colours: one place that says what "working", "done" and "failed" look like.
 *
 *   neutral  grey    nothing happening yet
 *   active   yellow  something is in progress (a file chosen, uploading, a step running)
 *   success  green   it worked
 *   error    red     it failed
 *
 * The colours come from theme tokens (--warning, --success, --danger in globals.css) that have
 * their own values for the light and dark themes, so they stay readable on both.
 * Tailwind needs complete class names in the source, so every class is spelled out in full here.
 */

export type Tone = "neutral" | "active" | "success" | "error";

export interface ToneStyle {
  /** Border colour of a bordered box. */
  border: string;
  /** Light background tint of a box. */
  tint: string;
  /** Text / icon colour. */
  text: string;
  /** Round icon badge: background and icon colour. */
  iconWrap: string;
  /** Solid fill, for progress bars. */
  bar: string;
}

export const TONE: Record<Tone, ToneStyle> = {
  neutral: {
    border: "border-border",
    tint: "",
    text: "text-text-muted",
    iconWrap: "bg-black/5 text-primary",
    bar: "bg-text-muted",
  },
  active: {
    border: "border-warning",
    tint: "bg-warning/5",
    text: "text-warning",
    iconWrap: "bg-warning/15 text-warning",
    bar: "bg-warning",
  },
  success: {
    border: "border-success",
    tint: "bg-success/5",
    text: "text-success",
    iconWrap: "bg-success/15 text-success",
    bar: "bg-success",
  },
  error: {
    border: "border-danger",
    tint: "bg-danger/5",
    text: "text-danger",
    iconWrap: "bg-danger/15 text-danger",
    bar: "bg-danger",
  },
};

/** "245 MB", "1.2 GB", "310 KB". */
export function formatBytes(bytes: number): string {
  if (!Number.isFinite(bytes) || bytes < 0) return "";
  if (bytes >= 1024 ** 3) return `${(bytes / 1024 ** 3).toFixed(1)} GB`;
  if (bytes >= 1024 ** 2) return `${Math.round(bytes / 1024 ** 2)} MB`;
  return `${Math.max(1, Math.round(bytes / 1024))} KB`;
}

// ── Upload flow decisions (pure, so they can be checked without a browser) ─────────────────────

/** Where the upload page is in its own flow. */
export type UploadStatus = "idle" | "uploading" | "processed" | "processing" | "success" | "error";

/** What the upload box shows. */
export type UploadPhase = "idle" | "ready" | "uploading" | "uploaded" | "starting" | "started" | "error";

export type FailedStep = "upload" | "process" | null;

export const PHASE_TONE: Record<UploadPhase, Tone> = {
  idle: "neutral",
  ready: "active",
  uploading: "active",
  uploaded: "success",
  starting: "success",
  started: "success",
  error: "error",
};

/** Which look the upload box should have (yellow = working, green = done, red = failed). */
export function uploadPhase(input: { status: UploadStatus; hasFile: boolean; hasError: boolean }): UploadPhase {
  switch (input.status) {
    case "uploading":
      return "uploading";
    case "processing":
      return "starting";
    case "success":
      return "started";
    case "processed":
      return "uploaded";
    case "error":
      return "error";
    default:
      // idle: a validation error (e.g. "not a video") has no file, but must still look like an error
      if (input.hasError && !input.hasFile) return "error";
      return input.hasFile ? "ready" : "idle";
  }
}

/** What the main button does. After a failure it repeats the step that failed, not always the upload. */
export function nextAction(input: { status: UploadStatus; failedStep: FailedStep; hasVideoId: boolean }): "upload" | "process" {
  if (input.status === "processed") return "process";
  if (input.status === "error" && input.failedStep === "process" && input.hasVideoId) return "process";
  return "upload";
}
