import { useQueryClient } from "@tanstack/react-query";
import {
  Archive,
  Building2,
  FileText,
  FolderSync,
  History,
  Inbox,
  Keyboard,
  Monitor,
  Moon,
  Search,
  Sun,
  Tag as TagIcon,
  Wrench,
} from "lucide-react";
import { useEffect, useMemo, useRef, useState, type KeyboardEvent, type ReactNode } from "react";
import { useNavigate } from "react-router";
import { api, type IngestReport } from "../api/client";
import { useDocumentSearch, useEntities, useTags } from "../api/hooks";
import { formatDate, shortName } from "../lib/format";
import { matchScore } from "../lib/text";
import { useTheme } from "../lib/theme";
import styles from "./CommandPalette.module.css";
import { toast } from "./toast";

interface Item {
  key: string;
  group: string;
  label: string;
  hint?: string;
  icon: ReactNode;
  run: () => void;
}

/** Ctrl+K: jump to any document, tag, entity or screen, or run an action, from the keyboard. */
export function CommandPalette({ onShowShortcuts }: { onShowShortcuts: () => void }) {
  const [open, setOpen] = useState(false);
  useEffect(() => {
    const onKey = (event: globalThis.KeyboardEvent) => {
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "k") {
        event.preventDefault();
        setOpen((v) => !v);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);
  return open ? <Palette onClose={() => setOpen(false)} onShowShortcuts={onShowShortcuts} /> : null;
}

function Palette({ onClose, onShowShortcuts }: { onClose: () => void; onShowShortcuts: () => void }) {
  const navigate = useNavigate();
  const client = useQueryClient();
  const [, setTheme] = useTheme();
  const tags = useTags().data;
  const entities = useEntities().data;
  const [query, setQuery] = useState("");
  const [debounced, setDebounced] = useState("");
  const [active, setActive] = useState(0);
  const dialog = useRef<HTMLDialogElement>(null);
  const list = useRef<HTMLUListElement>(null);

  useEffect(() => dialog.current?.showModal(), []);
  useEffect(() => {
    const timer = setTimeout(() => setDebounced(query), 150);
    return () => clearTimeout(timer);
  }, [query]);
  const documents = useDocumentSearch(debounced).data?.items;

  const items = useMemo(() => {
    const q = query.trim();
    const go = (to: string) => () => navigate(to);
    const commands: Item[] = [
      { key: "p:all", group: "Go to", label: "All documents", icon: <FileText size={16} />, run: go("/") },
      { key: "p:inbox", group: "Go to", label: "To be tagged", icon: <Inbox size={16} />, run: go("/?section=inbox") },
      { key: "p:outdated", group: "Go to", label: "Outdated", icon: <Archive size={16} />, run: go("/outdated") },
      { key: "p:tags", group: "Go to", label: "Manage tags", icon: <TagIcon size={16} />, run: go("/tags") },
      { key: "p:entities", group: "Go to", label: "Entities", icon: <Building2 size={16} />, run: go("/entities") },
      { key: "p:history", group: "Go to", label: "History", icon: <History size={16} />, run: go("/history") },
      { key: "p:maint", group: "Go to", label: "Maintenance", icon: <Wrench size={16} />, run: go("/maintenance") },
      {
        key: "a:scan",
        group: "Actions",
        label: "Scan the ingest folder now",
        icon: <FolderSync size={16} />,
        run: () =>
          api
            .post<IngestReport>("/ingest/scan")
            .then((r) => {
              void client.invalidateQueries({ queryKey: ["documents"] });
              void client.invalidateQueries({ queryKey: ["info"] });
              toast(
                `${r.imported.length} ${r.imported.length === 1 ? "document" : "documents"} imported.` +
                  (r.evernote?.length ? ` ${r.evernote.length} Evernote files wait for Import from Evernote (Maintenance).` : ""),
              );
            })
            .catch((e: Error) => toast(e.message, "error")),
      },
      { key: "a:light", group: "Actions", label: "Light theme", icon: <Sun size={16} />, run: () => setTheme("light") },
      { key: "a:dark", group: "Actions", label: "Dark theme", icon: <Moon size={16} />, run: () => setTheme("dark") },
      { key: "a:system", group: "Actions", label: "Theme like Windows", icon: <Monitor size={16} />, run: () => setTheme("system") },
      { key: "a:keys", group: "Actions", label: "Keyboard shortcuts", hint: "?", icon: <Keyboard size={16} />, run: onShowShortcuts },
    ];
    if (!q) return commands;

    const docItems: Item[] = (debounced.trim() ? documents ?? [] : []).slice(0, 6).map((d) => ({
      key: `d:${d.id}`,
      group: "Documents",
      label: d.title ?? shortName(d.display_name),
      hint: formatDate(d.document_date),
      icon: <FileText size={16} />,
      run: go(`/?doc=${d.id}`),
    }));
    const tagItems: Item[] = (tags ?? [])
      .map((t) => ({ t, score: matchScore(t.path, q) }))
      .filter((x) => x.score > 0)
      .sort((a, b) => b.score - a.score || b.t.total - a.t.total)
      .slice(0, 5)
      .map(({ t }) => ({
        key: `t:${t.id}`,
        group: "Tags",
        label: t.path,
        hint: t.total ? String(t.total) : undefined,
        icon: t.entity_id ? <Building2 size={16} /> : <TagIcon size={16} />,
        run: go(`/?tag=${t.id}`),
      }));
    const entityItems: Item[] = (entities ?? [])
      .map((e) => ({ e, score: matchScore([e.name, ...e.aliases].join(" "), q) }))
      .filter((x) => x.score > 0)
      .sort((a, b) => b.score - a.score)
      .slice(0, 4)
      .map(({ e }) => ({
        key: `e:${e.id}`,
        group: "Entities",
        label: e.name,
        hint: e.type_name,
        icon: <Building2 size={16} />,
        run: go(`/?entity=${e.id}&related=1`),
      }));
    const commandItems = commands.filter((c) => matchScore(c.label, q) > 0);
    const searchAll: Item = {
      key: "s:all",
      group: "Search",
      label: `Search all documents for “${q}”`,
      icon: <Search size={16} />,
      run: go(`/?q=${encodeURIComponent(q)}`),
    };
    return [...commandItems, ...docItems, searchAll, ...tagItems, ...entityItems];
  }, [query, debounced, documents, tags, entities, navigate, client, setTheme, onShowShortcuts]);

  const current = Math.min(active, items.length - 1);
  useEffect(() => {
    list.current?.querySelector(`[data-index="${current}"]`)?.scrollIntoView({ block: "nearest" });
  }, [current]);

  const run = (item: Item) => {
    dialog.current?.close();
    item.run();
  };
  const onKeyDown = (event: KeyboardEvent) => {
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      setActive((current + (event.key === "ArrowDown" ? 1 : -1) + items.length) % Math.max(items.length, 1));
    } else if (event.key === "Enter" && items[current]) {
      event.preventDefault();
      run(items[current]);
    }
  };

  let lastGroup = "";
  return (
    <dialog
      ref={dialog}
      className={styles.palette}
      onClose={onClose}
      onClick={(e) => e.target === dialog.current && dialog.current?.close()}
      aria-label="Command palette"
    >
      <div className={styles.inputRow}>
        <Search size={18} aria-hidden />
        <input
          autoFocus
          placeholder="Search documents, tags, entities, or type a command…"
          value={query}
          onChange={(e) => {
            setQuery(e.target.value);
            setActive(0);
          }}
          onKeyDown={onKeyDown}
          role="combobox"
          aria-expanded
          aria-controls="palette-list"
          aria-activedescendant={items[current] ? `palette-${current}` : undefined}
        />
        <kbd>Esc</kbd>
      </div>
      <ul id="palette-list" ref={list} role="listbox" className={styles.list}>
        {items.map((item, i) => {
          const heading = item.group !== lastGroup ? item.group : null;
          lastGroup = item.group;
          return (
            <li key={item.key} role="presentation">
              {heading && <div className={styles.group}>{heading}</div>}
              <div
                id={`palette-${i}`}
                data-index={i}
                role="option"
                aria-selected={i === current}
                className={i === current ? `${styles.item} ${styles.active}` : styles.item}
                onMouseMove={() => i !== current && setActive(i)}
                onClick={() => run(item)}
              >
                {item.icon}
                <span className={styles.label}>{item.label}</span>
                {item.hint && <span className={styles.hint}>{item.hint}</span>}
              </div>
            </li>
          );
        })}
        {query.trim() && debounced !== query && <li className={styles.loading}>Searching…</li>}
      </ul>
    </dialog>
  );
}
