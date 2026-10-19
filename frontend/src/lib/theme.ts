import { useSyncExternalStore } from "react";

export type ThemeChoice = "system" | "light" | "dark";
const KEY = "tabularium.theme";

function read(): ThemeChoice {
  try {
    const value = localStorage.getItem(KEY);
    return value === "light" || value === "dark" ? value : "system";
  } catch {
    return "system";
  }
}

function apply(choice: ThemeChoice) {
  const root = document.documentElement;
  if (choice === "system") root.removeAttribute("data-theme");
  else root.setAttribute("data-theme", choice);
}

let current = read();
apply(current);
const listeners = new Set<() => void>();

function setTheme(next: ThemeChoice) {
  current = next;
  apply(next);
  try {
    localStorage.setItem(KEY, next);
  } catch {
    /* private mode: fine, just not remembered */
  }
  listeners.forEach((l) => l());
}

/** Theme choice shared by the whole app and remembered per browser; "system" follows Windows. */
export function useTheme(): [ThemeChoice, (next: ThemeChoice) => void] {
  const choice = useSyncExternalStore(
    (listener) => {
      listeners.add(listener);
      return () => listeners.delete(listener);
    },
    () => current,
  );
  return [choice, setTheme];
}
