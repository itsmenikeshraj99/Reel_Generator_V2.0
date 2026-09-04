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
};
