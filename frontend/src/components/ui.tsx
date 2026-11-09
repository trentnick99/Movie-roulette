import { createContext, useCallback, useContext, useEffect, useRef, useState, type ReactNode } from "react";
import { Film, Loader2, X } from "lucide-react";
import { img } from "../lib/api";

/* ------------------------------------------------------------------ toasts */

interface Toast {
  id: number;
  text: string;
  tone: "info" | "error" | "ok";
}
const ToastCtx = createContext<(text: string, tone?: Toast["tone"]) => void>(() => {});

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([]);
  const push = useCallback((text: string, tone: Toast["tone"] = "info") => {
    const id = Date.now() + Math.random();
    setToasts((t) => [...t.slice(-3), { id, text, tone }]);
    window.setTimeout(() => setToasts((t) => t.filter((x) => x.id !== id)), tone === "error" ? 7000 : 3500);
  }, []);
  return (
    <ToastCtx.Provider value={push}>
      {children}
      <div className="fixed bottom-20 md:bottom-6 right-4 left-4 md:left-auto z-50 flex flex-col gap-2 items-end" aria-live="polite">
        {toasts.map((t) => (
          <div
            key={t.id}
            role={t.tone === "error" ? "alert" : "status"}
            className={`max-w-sm rounded-lg border px-4 py-3 text-sm shadow-2xl ${
              t.tone === "error"
                ? "border-miss/60 bg-[#2a1716] text-screen"
                : t.tone === "ok"
                  ? "border-hit/50 bg-[#18231a] text-screen"
                  : "border-line bg-panel-2 text-screen"
            }`}
          >
            {t.text}
          </div>
        ))}
      </div>
    </ToastCtx.Provider>
  );
}

export const useToast = () => useContext(ToastCtx);

/** Wrap an async action: shows errors as toasts and tracks busy state. */
export function useAction() {
  const toast = useToast();
  const [busy, setBusy] = useState(false);
  const run = useCallback(
    async <T,>(fn: () => Promise<T>, ok?: string): Promise<T | undefined> => {
      setBusy(true);
      try {
        const r = await fn();
        if (ok) toast(ok, "ok");
        return r;
      } catch (e) {
        toast((e as Error).message || "Something went wrong.", "error");
        return undefined;
      } finally {
        setBusy(false);
      }
    },
    [toast],
  );
  return { busy, run };
}

/* ------------------------------------------------------------------ layout bits */

export function PageHeader({ title, sub, actions }: { title: string; sub?: ReactNode; actions?: ReactNode }) {
  return (
    <header className="flex flex-wrap items-end justify-between gap-4 mb-6">
      <div>
        <h1 className="title text-3xl md:text-4xl">{title}</h1>
        {sub && <p className="text-dim mt-1 text-sm max-w-2xl">{sub}</p>}
      </div>
      {actions && <div className="flex flex-wrap gap-2">{actions}</div>}
    </header>
  );
}

export function Spinner({ label = "Loading" }: { label?: string }) {
  return (
    <div className="flex items-center gap-2 text-dim text-sm py-8" role="status">
      <Loader2 className="size-4 animate-spin" aria-hidden /> {label}
    </div>
  );
}

export function Empty({ title, children, action }: { title: string; children?: ReactNode; action?: ReactNode }) {
  return (
    <div className="surface p-8 text-center">
      <Film className="size-8 mx-auto text-faint mb-3" aria-hidden />
      <h2 className="title text-xl">{title}</h2>
      {children && <div className="text-dim text-sm mt-2 max-w-md mx-auto">{children}</div>}
      {action && <div className="mt-4">{action}</div>}
    </div>
  );
}

export function ErrorNote({ error }: { error: Error | null }) {
  if (!error) return null;
  return (
    <div role="alert" className="rounded-lg border border-miss/50 bg-[#2a1716] px-4 py-3 text-sm">
      {error.message}
    </div>
  );
}

export function Modal({
  open,
  onClose,
  title,
  children,
  wide = false,
}: {
  open: boolean;
  onClose: () => void;
  title: string;
  children: ReactNode;
  wide?: boolean;
}) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const prev = document.activeElement as HTMLElement | null;
    ref.current?.focus();
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => {
      window.removeEventListener("keydown", onKey);
      prev?.focus?.();
    };
  }, [open, onClose]);
  if (!open) return null;
  return (
    <div className="fixed inset-0 z-40 flex items-end md:items-center justify-center bg-black/70 p-0 md:p-6" onMouseDown={onClose}>
      <div
        ref={ref}
        tabIndex={-1}
        role="dialog"
        aria-modal="true"
        aria-label={title}
        onMouseDown={(e) => e.stopPropagation()}
        className={`surface w-full ${wide ? "md:max-w-4xl" : "md:max-w-lg"} max-h-[92vh] overflow-y-auto scroll-thin rounded-b-none md:rounded-b-[0.9rem] p-5 md:p-6 outline-none`}
      >
        <div className="flex items-start justify-between gap-4 mb-4">
          <h2 className="title text-2xl">{title}</h2>
          <button className="btn btn-ghost btn-sm" onClick={onClose} aria-label="Close">
            <X className="size-4" />
          </button>
        </div>
        {children}
      </div>
    </div>
  );
}

export function Poster({
  path,
  title,
  size = "w342",
  className = "",
}: {
  path: string | null | undefined;
  title: string;
  size?: string;
  className?: string;
}) {
  const [failed, setFailed] = useState(false);
  const src = img(path, size);
  return (
    <div className={`relative aspect-[2/3] overflow-hidden rounded-md bg-panel-2 ${className}`}>
      {src && !failed ? (
        <img src={src} alt={`Poster: ${title}`} loading="lazy" className="size-full object-cover" onError={() => setFailed(true)} />
      ) : (
        <div className="absolute inset-0 flex items-center justify-center p-3 text-center">
          <span className="title text-dim text-sm leading-snug">{title}</span>
        </div>
      )}
    </div>
  );
}

export function Toggle({
  checked,
  onChange,
  label,
  hint,
  disabled,
}: {
  checked: boolean;
  onChange: (v: boolean) => void;
  label: string;
  hint?: string;
  disabled?: boolean;
}) {
  return (
    <label className={`flex items-start gap-3 py-2 ${disabled ? "opacity-50" : "cursor-pointer"}`}>
      <input
        type="checkbox"
        className="mt-1 size-4 accent-[var(--color-lamp)]"
        checked={checked}
        disabled={disabled}
        onChange={(e) => onChange(e.target.checked)}
      />
      <span>
        <span className="text-sm">{label}</span>
        {hint && <span className="block text-xs text-dim mt-0.5">{hint}</span>}
      </span>
    </label>
  );
}

export function StatusDot({ ok, label }: { ok: boolean | null; label: string }) {
  return (
    <span className="flex items-center gap-2 text-sm">
      <span className={`size-2 rounded-full ${ok === null ? "bg-faint" : ok ? "bg-hit" : "bg-miss"}`} aria-hidden />
      {label}
    </span>
  );
}

export function TmdbAttribution({ compact = false }: { compact?: boolean }) {
  return (
    <p className={`text-faint ${compact ? "text-[11px]" : "text-xs"} leading-snug`}>
      Movie data and images from{" "}
      <a className="underline hover:text-dim" href="https://www.themoviedb.org/" target="_blank" rel="noreferrer">
        TMDB
      </a>
      . This product uses the TMDB API but is not endorsed or certified by TMDB.
    </p>
  );
}
