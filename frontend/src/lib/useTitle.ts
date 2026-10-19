import { useEffect } from "react";

/** Browser tab title: "<what> · Tabularium". */
export function useTitle(what: string | null | undefined) {
  useEffect(() => {
    document.title = what ? `${what} · Tabularium` : "Tabularium";
  }, [what]);
}
