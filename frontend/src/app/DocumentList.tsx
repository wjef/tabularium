import { Search, X } from "lucide-react";
import { useEffect, useState, type ReactNode } from "react";
import { ApiError, type DocumentSummary } from "../api/client";
import { useDocuments, useTags, type DocumentFilters } from "../api/hooks";
import { TagChip } from "../components/TagChip";
import { fileBadge, formatDate, shortName } from "../lib/format";
import styles from "./DocumentList.module.css";
import { useView } from "./useView";

const SORTS: { value: NonNullable<DocumentFilters["sort"]>; label: string }[] = [
  { value: "date", label: "Document date" },
  { value: "imported", label: "Newest imported" },
  { value: "imported_asc", label: "Oldest imported" },
  { value: "title", label: "Title" },
];

export function DocumentList() {
  const { section, filters, selected, update } = useView();
  const { data, isPending, isError, error, isPlaceholderData } = useDocuments(filters);
  const tags = useTags().data ?? [];

  // the box updates at once, the URL (and the search) a moment after typing stops
  const [query, setQuery] = useState(filters.q ?? "");
  const [urlQuery, setUrlQuery] = useState(filters.q ?? "");
  if (urlQuery !== (filters.q ?? "")) {
    // the URL changed from outside (Back, a link): show its search
    setUrlQuery(filters.q ?? "");
    setQuery(filters.q ?? "");
  }
  useEffect(() => {
    if (query === (filters.q ?? "")) return;
    const timer = setTimeout(() => update({ q: query }, { replace: true }), 250);
    return () => clearTimeout(timer);
  }, [query, filters.q, update]);

  const tag = filters.tag?.length === 1 ? tags.find((t) => t.id === filters.tag![0]) : undefined;
  const title = section === "inbox" ? "To be tagged" : tag ? tag.name : "All documents";
  const words = (filters.q ?? "").toLowerCase().split(/\s+/).filter((w) => w.length > 1);

  return (
    <section className={styles.pane} aria-label="Documents">
      <header className={styles.header}>
        {tag && tag.path !== tag.name && <div className={styles.crumb}>{tag.path.split(" / ").slice(0, -1).join(" / ")}</div>}
        <h1 className={styles.title}>{title}</h1>
        <div className={styles.meta}>
          <span>
            {data ? `${data.total} ${data.total === 1 ? "document" : "documents"}` : " "}
          </span>
          <select
            className={styles.sort}
            value={filters.sort}
            onChange={(e) => update({ sort: e.target.value })}
            aria-label="Sort by"
          >
            {SORTS.map((s) => (
              <option key={s.value} value={s.value}>
                {s.label}
              </option>
            ))}
          </select>
        </div>
        <label className={styles.search}>
          <Search size={15} aria-hidden />
          <input
            type="search"
            placeholder="Search titles and text"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            aria-label="Search"
          />
          {query && (
            <button onClick={() => setQuery("")} aria-label="Clear search">
              <X size={14} />
            </button>
          )}
        </label>
      </header>

      <div className={isPlaceholderData ? `${styles.list} ${styles.loading}` : styles.list}>
        {isPending && <Skeleton />}
        {isError && (
          <p className={styles.message}>
            {error instanceof ApiError && error.status === 422
              ? "This link points to a filter that no longer exists."
              : `Could not load documents: ${error.message}`}
          </p>
        )}
        {data && data.items.length === 0 && (
          <p className={styles.message}>
            {filters.q ? "Nothing matches this search." : section === "inbox" ? "Everything is tagged." : "No documents here yet."}
          </p>
        )}
        {data?.items.map((doc) => (
          <DocumentCard
            key={doc.id}
            doc={doc}
            words={words}
            selected={doc.id === selected}
            onSelect={() => update({ doc: doc.id })}
          />
        ))}
        {data && data.total > data.items.length && (
          <p className={styles.more}>
            Showing the first {data.items.length} of {data.total}. Narrow the search to see the rest.
          </p>
        )}
      </div>
    </section>
  );
}

interface CardProps {
  doc: DocumentSummary;
  words: string[];
  selected: boolean;
  onSelect: () => void;
}

function DocumentCard({ doc, words, selected, onSelect }: CardProps) {
  const name = doc.title ?? shortName(doc.display_name);
  return (
    <button
      className={selected ? `${styles.card} ${styles.selected}` : styles.card}
      onClick={onSelect}
      aria-current={selected ? "true" : undefined}
    >
      <div className={styles.cardTop}>
        <span className={doc.title ? styles.cardTitle : `${styles.cardTitle} ${styles.untitled}`}>
          {highlight(name, words)}
        </span>
        <span className={styles.badge} data-kind={doc.preview}>
          {fileBadge(doc.stored_name)}
        </span>
      </div>
      <div className={styles.date}>{formatDate(doc.document_date) || `Imported ${formatDate(doc.ingested_at)}`}</div>
      {doc.snippet && <p className={styles.snippet}>{highlight(doc.snippet, words)}</p>}
      {doc.tags.length > 0 && (
        <div className={styles.tags}>
          {doc.tags.slice(0, 4).map((t) => (
            <TagChip key={t.id} tag={t} />
          ))}
          {doc.tags.length > 4 && <span className={styles.moreTags}>+{doc.tags.length - 4}</span>}
        </div>
      )}
    </button>
  );
}

/** Marks the searched words in a piece of text. */
function highlight(text: string, words: string[]): ReactNode {
  if (!words.length) return text;
  const escaped = words.map((w) => w.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"));
  const parts = text.split(new RegExp(`(${escaped.join("|")})`, "gi"));
  return parts.map((part, i) => (i % 2 ? <mark key={i}>{part}</mark> : part));
}

function Skeleton() {
  return (
    <>
      {[0, 1, 2, 3, 4].map((i) => (
        <div key={i} className={styles.skeleton} />
      ))}
    </>
  );
}
