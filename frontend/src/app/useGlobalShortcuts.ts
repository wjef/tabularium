import { useEffect } from "react";
import { useNavigate } from "react-router";
import { isTyping } from "../lib/keys";

const GO: Record<string, string> = {
  a: "/",
  i: "/?section=inbox",
  t: "/tags",
  e: "/entities",
  h: "/history",
  m: "/maintenance",
  o: "/outdated",
};

/** "?" shows the shortcut list; "G" followed by a letter jumps to a screen (G A = all documents…). */
export function useGlobalShortcuts(toggleHelp: () => void) {
  const navigate = useNavigate();
  useEffect(() => {
    let pendingG = 0;
    const onKey = (event: KeyboardEvent) => {
      if (event.ctrlKey || event.metaKey || event.altKey || isTyping(event)) return;
      if (document.querySelector("dialog[open]") && event.key !== "?") return;
      if (event.key === "?") {
        event.preventDefault();
        toggleHelp();
      } else if (event.key === "g") {
        pendingG = Date.now();
      } else if (pendingG && Date.now() - pendingG < 1200 && GO[event.key]) {
        event.preventDefault();
        event.stopImmediatePropagation();
        pendingG = 0;
        navigate(GO[event.key]);
      } else {
        pendingG = 0;
      }
    };
    window.addEventListener("keydown", onKey, { capture: true });
    return () => window.removeEventListener("keydown", onKey, { capture: true });
  }, [navigate, toggleHelp]);
}
