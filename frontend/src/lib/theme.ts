import { useEffect, useState } from "react";

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

/** Theme choice remembered per browser; "system" follows Windows' light/dark setting. */
export function useTheme(): [ThemeChoice, (next: ThemeChoice) => void] {
  const [choice, setChoice] = useState<ThemeChoice>(read);
  useEffect(() => {
    apply(choice);
    try {
      localStorage.setItem(KEY, choice);
    } catch {
      /* private mode: fine, just not remembered */
    }
  }, [choice]);
  return [choice, setChoice];
}
