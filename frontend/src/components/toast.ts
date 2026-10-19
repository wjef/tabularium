import { useSyncExternalStore } from "react";

export interface ToastAction {
  label: string;
  run: () => void;
}

export interface Toast {
  id: number;
  message: string;
  kind: "info" | "error";
  action?: ToastAction;
}

let toasts: Toast[] = [];
let nextId = 1;
const listeners = new Set<() => void>();
const emit = () => listeners.forEach((l) => l());

export function dismiss(id: number) {
  toasts = toasts.filter((t) => t.id !== id);
  emit();
}

/** Short message at the bottom of the screen; errors and ones with an action stay longer. */
export function toast(message: string, kind: Toast["kind"] = "info", action?: ToastAction) {
  const id = nextId++;
  toasts = [...toasts.slice(-3), { id, message, kind, action }];
  emit();
  setTimeout(() => dismiss(id), kind === "error" ? 8000 : action ? 7000 : 4000);
}

export function useToasts(): Toast[] {
  return useSyncExternalStore(
    (listener) => {
      listeners.add(listener);
      return () => listeners.delete(listener);
    },
    () => toasts,
  );
}
