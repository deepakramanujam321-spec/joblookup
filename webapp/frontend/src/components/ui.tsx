// Small, dependency-free UI primitives shared by every screen.
import { createContext, useCallback, useContext, useEffect, useId, useRef, useState, type ReactNode } from "react";
import { ApiError } from "../api/client";
import { absolute, relative, scoreTone } from "../lib/format";

export function Score({ value, label }: { value: number | null | undefined; label?: string }) {
  const tone = scoreTone(value);
  return (
    <span className={`score ${tone}`} title={label ?? (value == null ? "Not scored yet" : `${Math.round(value)} / 100`)}>
      {value == null ? "—" : Math.round(value)}
    </span>
  );
}

export function Bar({ value }: { value: number | null }) {
  const tone = scoreTone(value);
  return (
    <div className={`bar ${tone}`} role="presentation">
      <span style={{ width: `${Math.max(0, Math.min(100, value ?? 0))}%` }} />
    </div>
  );
}

export function Badge({ tone = "", children, title }: { tone?: "" | "good" | "warn" | "bad" | "info" | "accent"; children: ReactNode; title?: string }) {
  return <span className={`badge ${tone}`} title={title}>{children}</span>;
}

export function Time({ iso, prefix = "", fallback = "—" }: { iso: string | null | undefined; prefix?: string; fallback?: string }) {
  const rel = relative(iso);
  if (!rel || !iso) return <span className="muted">{fallback}</span>;
  return <time dateTime={iso} title={absolute(iso)}>{prefix}{rel}</time>;
}

export function Spinner({ label = "Loading" }: { label?: string }) {
  return <span className="spinner" role="status" aria-label={label} />;
}

export function Skeleton({ height = 16, width = "100%" }: { height?: number; width?: number | string }) {
  return <div className="skeleton" style={{ height, width }} aria-hidden="true" />;
}

export function EmptyState({ title, children, action }: { title: string; children?: ReactNode; action?: ReactNode }) {
  return (
    <div className="empty">
      <h3>{title}</h3>
      {children && <p>{children}</p>}
      {action}
    </div>
  );
}

export function ErrorState({ error, onRetry }: { error: unknown; onRetry?: () => void }) {
  const message = error instanceof ApiError ? error.message : "Something went wrong loading this.";
  return (
    <div className="notice bad" role="alert">
      <div className="grow">{message}</div>
      {onRetry && <button className="btn sm" onClick={onRetry}>Retry</button>}
    </div>
  );
}

export function errorMessage(error: unknown): string {
  return error instanceof ApiError ? error.message : error instanceof Error ? error.message : "Something went wrong.";
}

// ------------------------------------------------------------------ dialog

export function Modal({ title, onClose, children, footer }: { title: string; onClose: () => void; children: ReactNode; footer?: ReactNode }) {
  const ref = useRef<HTMLDivElement>(null);
  const id = useId();
  useEffect(() => {
    const previouslyFocused = document.activeElement as HTMLElement | null;
    const first = ref.current?.querySelector<HTMLElement>("input, textarea, select, button");
    first?.focus();
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("keydown", onKey);
      previouslyFocused?.focus();
    };
  }, [onClose]);
  return (
    <div className="modal-backdrop" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div className="modal" role="dialog" aria-modal="true" aria-labelledby={id} ref={ref}>
        <h2 id={id}>{title}</h2>
        {children}
        {footer && <div className="row" style={{ justifyContent: "flex-end" }}>{footer}</div>}
      </div>
    </div>
  );
}

type ConfirmState = { title: string; body: ReactNode; confirmLabel: string; danger?: boolean; resolve: (ok: boolean) => void } | null;
const ConfirmContext = createContext<(opts: { title: string; body: ReactNode; confirmLabel?: string; danger?: boolean }) => Promise<boolean>>(
  async () => true,
);

export function ConfirmProvider({ children }: { children: ReactNode }) {
  const [state, setState] = useState<ConfirmState>(null);
  const confirm = useCallback(
    (opts: { title: string; body: ReactNode; confirmLabel?: string; danger?: boolean }) =>
      new Promise<boolean>((resolve) => setState({ confirmLabel: "Confirm", ...opts, resolve })),
    [],
  );
  const close = (ok: boolean) => {
    state?.resolve(ok);
    setState(null);
  };
  return (
    <ConfirmContext.Provider value={confirm}>
      {children}
      {state && (
        <Modal title={state.title} onClose={() => close(false)} footer={<>
          <button className="btn" onClick={() => close(false)}>Cancel</button>
          <button className={`btn ${state.danger ? "danger" : "primary"}`} onClick={() => close(true)}>{state.confirmLabel}</button>
        </>}>
          <div className="muted">{state.body}</div>
        </Modal>
      )}
    </ConfirmContext.Provider>
  );
}

export const useConfirm = () => useContext(ConfirmContext);

// ------------------------------------------------------------------ toasts

type Toast = { id: number; message: string; kind: "info" | "error" };
const ToastContext = createContext<(message: string, kind?: "info" | "error") => void>(() => {});

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([]);
  const push = useCallback((message: string, kind: "info" | "error" = "info") => {
    const id = Date.now() + Math.random();
    setToasts((t) => [...t, { id, message, kind }]);
    setTimeout(() => setToasts((t) => t.filter((x) => x.id !== id)), kind === "error" ? 7000 : 3500);
  }, []);
  return (
    <ToastContext.Provider value={push}>
      {children}
      <div className="toasts" aria-live="polite">
        {toasts.map((t) => (
          <div key={t.id} className={`toast ${t.kind === "error" ? "error" : ""}`} role={t.kind === "error" ? "alert" : "status"}>
            <span className="grow">{t.message}</span>
            <button className="btn ghost sm" style={{ color: "inherit" }} onClick={() => setToasts((x) => x.filter((y) => y.id !== t.id))} aria-label="Dismiss">✕</button>
          </div>
        ))}
      </div>
    </ToastContext.Provider>
  );
}

export const useToast = () => useContext(ToastContext);

// ---------------------------------------------------------------- inputs

export function ListInput({ value, onChange, placeholder, id }: { value: string[]; onChange: (v: string[]) => void; placeholder?: string; id?: string }) {
  // Comma/newline separated list editing that keeps the user's typing intact.
  const [text, setText] = useState(value.join(", "));
  useEffect(() => setText(value.join(", ")), [value.join("\u0000")]); // eslint-disable-line react-hooks/exhaustive-deps
  return (
    <input id={id} className="input" value={text} placeholder={placeholder}
      onChange={(e) => setText(e.target.value)}
      onBlur={() => onChange(text.split(/[,\n]/).map((s) => s.trim()).filter(Boolean))} />
  );
}
