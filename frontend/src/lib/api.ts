import { supabase } from "./supabase";

const API_BASE_URL =
  process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000/api";

async function getAuthHeaders(): Promise<Record<string, string>> {
  const {
    data: { session },
  } = await supabase.auth.getSession();
  const token = session?.access_token;
  return {
    "Content-Type": "application/json",
    ...(token ? { Authorization: `Bearer ${token}` } : {}),
  };
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = await getAuthHeaders();
  const res = await fetch(`${API_BASE_URL}${path}`, {
    ...init,
    headers: { ...headers, ...(init.headers || {}) },
  });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      detail = body.detail || detail;
    } catch {
      // non-JSON error body
    }
    throw new Error(detail || "Request failed");
  }
  return res.json() as Promise<T>;
}

export interface UploadUrlResponse {
  video_id: string;
  storage_path: string;
  bucket: string;
  message: string;
  expires_at: string;
  // Phase 11: signed upload URL for the progress-bar flow.
  // Optional in the type because older backends (pre-PR1) don't return
  // it. PR5's upload code checks for this field; if absent, it falls
  // back to the legacy supabase.storage.upload() path.
  signed_upload_url?: string;
  upload_token?: string;
}

export interface ProcessResponse {
  message: string;
  video_id: string;
  task_id: string;
}

export interface StatusResponse {
  status: string;
  stage: string | null;
  error: string | null;
}

export interface Reel {
  id?: string;
  title: string | null;
  url: string;
  storage_path?: string;
}

export interface DeleteResponse {
  message: string;
  video_id: string;
  deleted_storage_paths: number;
}

// Phase 11 — dashboard list
export type VideoStatus =
  | "PENDING_UPLOAD"
  | "UPLOADED"
  | "PROCESSING"
  | "READY"
  | "FAILED";

export interface VideoListItem {
  id: string;
  filename: string;
  status: VideoStatus | string; // backend may evolve; treat as opaque
  created_at: string;
  expires_at: string;
  reel_count: number;
  last_stage: string | null;
}

export interface VideoListResponse {
  videos: VideoListItem[];
  total: number;
}

export const api = {
  async uploadUrl(filename: string): Promise<UploadUrlResponse> {
    return request<UploadUrlResponse>("/videos/upload-url", {
      method: "POST",
      body: JSON.stringify({ filename }),
    });
  },

  async processVideo(videoId: string): Promise<ProcessResponse> {
    return request<ProcessResponse>(`/videos/${videoId}/process`, {
      method: "POST",
      body: JSON.stringify({}),
    });
  },

  async getVideoStatus(videoId: string): Promise<StatusResponse> {
    return request<StatusResponse>(`/videos/${videoId}/status`);
  },

  async getReels(videoId: string): Promise<{ reels: Reel[] }> {
    return request<{ reels: Reel[] }>(`/reels/${videoId}`);
  },

  // Tier-1 "instant kill". Backend handles the original video + all
  // reels + the DB row (cascade). This replaces the old frontend
  // approach of calling supabase.storage.remove() then deleting the
  // videos row separately — which leaked the original upload.
  async deleteVideo(videoId: string): Promise<DeleteResponse> {
    return request<DeleteResponse>(`/videos/${videoId}`, {
      method: "DELETE",
    });
  },

  // Phase 11 — list the caller's videos for the dashboard.
  // Uses GET /api/videos (added in PR 1 of the UI overhaul).
  async getMyVideos(
    limit = 50,
    offset = 0,
  ): Promise<VideoListResponse> {
    return request<VideoListResponse>(
      `/videos?limit=${limit}&offset=${offset}`,
    );
  },
};

/**
 * Phase 11 — PUT a file to a Supabase signed-upload URL with progress
 * reporting.
 *
 * Why not `fetch`: fetch() doesn't expose `onprogress` for the upload
 * body. XHR does. ~25 lines, no deps.
 *
 * The signed URL is short-lived (Supabase default ~2 min). It already
 * carries the `token` in its query string, so we don't need an
 * Authorization header. Just PUT raw bytes.
 */
export interface UploadProgress {
  loaded: number;
  total: number;
  pct: number;
}

export async function uploadFileToSignedUrl(
  url: string,
  file: File,
  contentType: string,
  onProgress: (p: UploadProgress) => void,
): Promise<void> {
  return new Promise<void>((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("PUT", url);
    xhr.setRequestHeader("Content-Type", contentType);
    xhr.setRequestHeader("x-upsert", "true");

    xhr.upload.onprogress = (e) => {
      if (!e.lengthComputable) return;
      const pct = Math.round((e.loaded / e.total) * 100);
      onProgress({ loaded: e.loaded, total: e.total, pct });
    };

    xhr.onload = () => {
      if (xhr.status >= 200 && xhr.status < 300) {
        resolve();
      } else {
        reject(new Error(`Upload failed (${xhr.status})`));
      }
    };
    xhr.onerror = () => reject(new Error("Network error during upload"));
    xhr.onabort = () => reject(new Error("Upload cancelled"));

    xhr.send(file);
  });
}
