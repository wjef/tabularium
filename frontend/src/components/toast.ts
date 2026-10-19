import { useSyncExternalStore } from "react";

export interface Toast {
  id: number;
  message: string;
  kind: "info" | "error";
}

let toasts: Toast[] = [];
let nextId = 1;
const listeners = new Set<() => void>();
const emit = () => listeners.forEach((l) => l());

export function dismiss(id: number) {
  toasts = toasts.filter((t) => t.id !== id);
  emit();
}

/** Short message at the bottom of the screen; errors stay a little longer. */
export function toast(message: string, kind: Toast["kind"] = "info") {
  const id = nextId++;
  toasts = [...toasts.slice(-3), { id, message, kind }];
  emit();
  setTimeout(() => dismiss(id), kind === "error" ? 8000 : 4000);
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
