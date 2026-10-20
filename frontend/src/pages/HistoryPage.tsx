import { useQuery } from "@tanstack/react-query";
import { Search } from "lucide-react";
import { useMemo, useState } from "react";
import { useNavigate } from "react-router";
import { api, type HistoryEntry } from "../api/client";
import { Empty, Page } from "../components/Page";
import { shortName } from "../lib/format";
import { matchScore } from "../lib/text";
import ui from "../styles/ui.module.css";
import styles from "./HistoryPage.module.css";

const OBJECTS: Record<string, string> = {
  document: "Document",
  tag: "Tag",
  entity: "Entity",
  entity_type: "Entity type",
};

const ACTIONS: Record<string, string> = {
  CREATE: "created",
  UPDATE: "changed",
  MOVE: "moved",
  MERGE: "merged",
  DELETE: "deleted",
  TAG_ADD: "tag added",
  TAG_REMOVE: "tag removed",
  MEMBER_ADD: "added to group",
  MEMBER_REMOVE: "removed from group",
  RELATION_ADD: "linked",
  RELATION_REMOVE: "unlinked",
  OUTDATE: "outdated",
  RESTORE: "restored",
};

const time = new Intl.DateTimeFormat("en-GB", { dateStyle: "medium", timeStyle: "short" });

/** What changed, in a few words: "title: Old → New". */
function describe(entry: HistoryEntry): string {
  const oldValue = (entry.old_value ?? {}) as Record<string, unknown>;
  const newValue = (entry.new_value ?? {}) as Record<string, unknown>;
  if (entry.action === "TAG_ADD" || entry.action === "TAG_REMOVE") {
    return String(newValue.tag ?? oldValue.tag ?? "");
  }
  if (entry.action !== "UPDATE") return "";
  const keys = [...new Set([...Object.keys(oldValue), ...Object.keys(newValue)])].filter(
    (k) => JSON.stringify(oldValue[k]) !== JSON.stringify(newValue[k]),
  );
  const show = (v: unknown) =>
    v === null || v === undefined || v === "" || (Array.isArray(v) && !v.length)
      ? "—"
      : Array.isArray(v)
        ? v.join(", ")
        : typeof v === "object"
          ? JSON.stringify(v)
          : String(v);
  return keys.map((k) => `${k.replace(/_/g, " ")}: ${show(oldValue[k])} → ${show(newValue[k])}`).join("; ");
}

/** Every change ever made, newest first (the audit log). */
export function HistoryPage() {
  const navigate = useNavigate();
  const [kind, setKind] = useState("");
  const [filter, setFilter] = useState("");
  const { data, isPending } = useQuery({
    queryKey: ["history"],
    queryFn: () => api.get<HistoryEntry[]>("/history?limit=1000"),
  });
  const rows = useMemo(
    () =>
      (data ?? []).filter(
        (e) =>
          (!kind || e.object_type === kind) &&
          (!filter.trim() || matchScore(`${e.label ?? ""} ${e.actor} ${ACTIONS[e.action] ?? e.action} ${describe(e)}`, filter) > 0),
      ),
    [data, kind, filter],
  );

  return (
    <Page title="History" subtitle="Every change is recorded here; nothing is ever silently lost.">
      <div className={`${ui.row} ${styles.filters}`}>
        <label className={styles.search}>
          <Search size={14} aria-hidden />
          <input placeholder="Filter" value={filter} onChange={(e) => setFilter(e.target.value)} aria-label="Filter history" />
        </label>
        <select className={ui.select} value={kind} onChange={(e) => setKind(e.target.value)} aria-label="Kind of object">
          <option value="">Everything</option>
          {Object.entries(OBJECTS).map(([value, label]) => (
            <option key={value} value={value}>
              {label}s
            </option>
          ))}
        </select>
      </div>
      {isPending && <Empty>Loading…</Empty>}
      {data && !rows.length && <Empty>Nothing matches.</Empty>}
      {rows.length > 0 && (
        <div className={styles.tableWrap}>
          <table className={ui.table}>
            <thead>
              <tr>
                <th>When</th>
                <th>What</th>
                <th>Change</th>
                <th>Who</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((e) => (
                <tr key={e.id}>
                  <td className={styles.when}>{time.format(new Date(e.at))}</td>
                  <td>
                    <span className={ui.muted}>{OBJECTS[e.object_type] ?? e.object_type} </span>
                    {e.link ? (
                      <button className={styles.link} onClick={() => navigate(e.link!)}>
                        {shortName(e.label ?? "")}
                      </button>
                    ) : (
                      <span className={ui.muted}>{e.label ?? "(no longer exists)"}</span>
                    )}
                  </td>
                  <td>
                    <span className={ui.pill}>{ACTIONS[e.action] ?? e.action}</span>{" "}
                    <span className={styles.detail}>{describe(e)}</span>
                  </td>
                  <td className={ui.muted}>{e.actor}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Page>
  );
}
