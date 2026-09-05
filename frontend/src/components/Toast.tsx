"use client";

/**
 * Toast notifications — provider + hook.
 *
 * Why a custom impl: only need 3 variants (success / error / info) and
 * a 4-line API. Adding `react-hot-toast` would be 12KB gzipped for what
 * is 80 lines of code here.
 *
 * Z-index: toasts render at z-[100] so they sit above the page content
 * but the delete-confirm modal (z-40) stays modal. When a modal IS
 * open, new toasts still appear above it briefly — acceptable, since
 * the modal blocks input.
 */

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { X, CheckCircle2, AlertCircle, Info } from "lucide-react";

import { cn } from "@/lib/cn";

type ToastVariant = "success" | "error" | "info";

interface Toast {
  id: string;
  message: string;
  variant: ToastVariant;
}

interface ToastContextValue {
  toast: (message: string, variant?: ToastVariant) => void;
  success: (message: string) => void;
  error: (message: string) => void;
  info: (message: string) => void;
  dismiss: (id: string) => void;
}

const ToastContext = createContext<ToastContextValue | null>(null);

const DEFAULT_TTL_MS = 4000;
const ERROR_TTL_MS = 6000;

export function ToastProvider({ children }: { children: React.ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([]);
  const timers = useRef<Map<string, ReturnType<typeof setTimeout>>>(new Map());

  const dismiss = useCallback((id: string) => {
    setToasts((cur) => cur.filter((t) => t.id !== id));
    const t = timers.current.get(id);
    if (t) {
      clearTimeout(t);
      timers.current.delete(id);
    }
  }, []);

  const toast = useCallback(
    (message: string, variant: ToastVariant = "info") => {
      const id = `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
      setToasts((cur) => [...cur, { id, message, variant }]);
      const ttl = variant === "error" ? ERROR_TTL_MS : DEFAULT_TTL_MS;
      const handle = setTimeout(() => dismiss(id), ttl);
      timers.current.set(id, handle);
    },
    [dismiss],
  );

  // Cleanup on unmount
  useEffect(() => {
    const map = timers.current;
    return () => {
      map.forEach((h) => clearTimeout(h));
      map.clear();
    };
  }, []);

  const value = useMemo<ToastContextValue>(
    () => ({
      toast,
      success: (m) => toast(m, "success"),
      error: (m) => toast(m, "error"),
      info: (m) => toast(m, "info"),
      dismiss,
    }),
    [toast, dismiss],
  );

  return (
    <ToastContext.Provider value={value}>
      {children}
      <ToastViewport toasts={toasts} onDismiss={dismiss} />
    </ToastContext.Provider>
  );
}

export function useToast(): ToastContextValue {
  const ctx = useContext(ToastContext);
  if (!ctx) {
    // Fail open: in case someone forgets the provider (e.g. in a test),
    // log to console so we don't crash. Production should always have
    // the provider in `app/layout.tsx`.
    return {
      toast: (m) => console.log("[toast]", m),
      success: (m) => console.log("[toast:success]", m),
      error: (m) => console.error("[toast:error]", m),
      info: (m) => console.log("[toast:info]", m),
      dismiss: () => {},
    };
  }
  return ctx;
}

function ToastViewport({
  toasts,
  onDismiss,
}: {
  toasts: Toast[];
  onDismiss: (id: string) => void;
}) {
  return (
    <div
      aria-live="polite"
      aria-atomic="true"
      className="fixed top-4 right-4 z-[100] flex flex-col gap-2 pointer-events-none"
    >
      {toasts.map((t) => (
        <ToastItem key={t.id} toast={t} onDismiss={() => onDismiss(t.id)} />
      ))}
    </div>
  );
}

function ToastItem({
  toast,
  onDismiss,
}: {
  toast: Toast;
  onDismiss: () => void;
}) {
  const Icon =
    toast.variant === "success"
      ? CheckCircle2
      : toast.variant === "error"
      ? AlertCircle
      : Info;

  const colors =
    toast.variant === "success"
      ? "border-green-500/30 bg-green-500/10 text-green-100"
      : toast.variant === "error"
      ? "border-red-500/30 bg-red-500/10 text-red-100"
      : "border-border bg-black/5 text-text";

  return (
    <div
      role="status"
      className={cn(
        "pointer-events-auto flex items-start gap-3 min-w-[260px] max-w-sm",
        "px-4 py-3 rounded-xl border backdrop-blur-md shadow-2xl",
        "animate-toast-in",
        colors,
      )}
    >
      <Icon size={18} className="mt-0.5 shrink-0" />
      <p className="text-sm flex-1 leading-snug">{toast.message}</p>
      <button
        onClick={onDismiss}
        aria-label="Dismiss"
        className="shrink-0 opacity-60 hover:opacity-100 transition-opacity"
      >
        <X size={16} />
      </button>
    </div>
  );
}
