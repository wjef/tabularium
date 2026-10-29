import { useCallback, useMemo } from "react";
import { useSearchParams } from "react-router";
import type { DocumentFilters } from "../api/hooks";

export type Section = "all" | "inbox" | "trash";

const SECTIONS: Section[] = ["all", "inbox", "trash"];

/** Everything the workspace shows lives in the URL: shareable, and Back/Forward work. */
export function useView() {
  const [params, setParams] = useSearchParams();

  const view = useMemo(() => {
    const section = SECTIONS.find((s) => s === params.get("section")) ?? "all";
    const filters: DocumentFilters = {
      q: params.get("q") ?? "",
      tag: params.getAll("tag"),
      entity: params.getAll("entity"),
      related: params.get("related") === "1",
      // documents being tagged are only shown in To be tagged; Trash shows its documents either way
      tagging: section === "inbox" ? "todo" : section === "trash" ? "any" : "done",
      ...(section === "trash" && { trash: true }),
      sort:
        (params.get("sort") as DocumentFilters["sort"]) ??
        (section === "inbox" ? "imported_asc" : section === "trash" ? "imported" : "date"),
    };
    return { section, filters, selected: params.get("doc") };
  }, [params]);

  const update = useCallback(
    (changes: Record<string, string | string[] | null>, options?: { replace?: boolean }) => {
      setParams(
        (prev) => {
          const next = new URLSearchParams(prev);
          for (const [key, value] of Object.entries(changes)) {
            next.delete(key);
            if (Array.isArray(value)) value.forEach((v) => next.append(key, v));
            else if (value !== null && value !== "") next.set(key, value);
          }
          return next;
        },
        { replace: options?.replace },
      );
    },
    [setParams],
  );

  return { ...view, update };
}
