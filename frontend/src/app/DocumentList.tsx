import { Search, X } from "lucide-react";
import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { ApiError, thumbnailUrl, type DocumentSummary } from "../api/client";
import { useDocuments, useTags, type DocumentFilters } from "../api/hooks";
import { TagChip } from "../components/TagChip";
import { fileBadge, formatDate, shortName } from "../lib/format";
import { isTyping } from "../lib/keys";
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
  const { data, isPending, isError, error, isPlaceholderData, hasNextPage, fetchNextPage, isFetchingNextPage } =
    useDocuments(filters);
  const tags = useTags().data ?? [];
  const items = useMemo(() => data?.pages.flatMap((p) => p.items) ?? [], [data]);
  const total = data?.pages[0]?.total;

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

  // load the next page when the end of the list comes into view
  const sentinel = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const node = sentinel.current;
    if (!node || !hasNextPage) return;
    const observer = new IntersectionObserver(
      (entries) => {
        if (entries[0].isIntersecting && !isFetchingNextPage) void fetchNextPage();
      },
      { rootMargin: "400px" },
    );
    observer.observe(node);
    return () => observer.disconnect();
  }, [hasNextPage, isFetchingNextPage, fetchNextPage]);

  // ↑/↓ or K/J move through the list; "/" jumps to the search box
  const searchBox = useRef<HTMLInputElement>(null);
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.ctrlKey || event.metaKey || event.altKey) return;
      if (event.key === "/" && !isTyping(event)) {
        event.preventDefault();
        searchBox.current?.focus();
        return;
      }
      if (event.key === "Escape" && event.target === searchBox.current) {
        searchBox.current?.blur();
        return;
      }
      const step = { ArrowDown: 1, j: 1, ArrowUp: -1, k: -1 }[event.key];
      if (!step || isTyping(event) || !items.length) return;
      event.preventDefault();
      const index = items.findIndex((d) => d.id === selected);
      const next = index < 0 ? 0 : Math.min(Math.max(index + step, 0), items.length - 1);
      if (items[next].id !== selected) update({ doc: items[next].id }, { replace: true });
      if (next >= items.length - 3 && hasNextPage) void fetchNextPage();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [items, selected, update, hasNextPage, fetchNextPage]);

  const tag = filters.tag?.length === 1 ? tags.find((t) => t.id === filters.tag![0]) : undefined;
  const title = section === "inbox" ? "To be tagged" : tag ? tag.name : "All documents";
  const words = (filters.q ?? "").toLowerCase().split(/\s+/).filter((w) => w.length > 1);

  return (
    <section className={styles.pane} aria-label="Documents">
      <header className={styles.header}>
        {tag && tag.path !== tag.name && (
          <div className={styles.crumb}>{tag.path.split(" / ").slice(0, -1).join(" / ")}</div>
        )}
        <h1 className={styles.title}>{title}</h1>
        <div className={styles.meta}>
          <span>{total !== undefined ? `${total} ${total === 1 ? "document" : "documents"}` : " "}</span>
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
            ref={searchBox}
            type="search"
            placeholder="Search titles and text"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            aria-label="Search"
          />
          {query ? (
            <button onClick={() => setQuery("")} aria-label="Clear search">
              <X size={14} />
            </button>
          ) : (
            <kbd className={styles.kbd} aria-hidden>
              /
            </kbd>
          )}
        </label>
      </header>

      <div className={isPlaceholderData ? `${styles.list} ${styles.loading}` : styles.list} role="list">
        {isPending && <Skeleton />}
        {isError && (
          <p className={styles.message}>
            {error instanceof ApiError && error.status === 422
              ? "This link points to a filter that no longer exists."
              : `Could not load documents: ${error.message}`}
          </p>
        )}
        {data && items.length === 0 && (
          <p className={styles.message}>
            {filters.q
              ? "Nothing matches this search."
              : section === "inbox"
                ? "Everything is tagged."
                : "No documents here yet."}
          </p>
        )}
        {items.map((doc) => (
          <DocumentCard
            key={doc.id}
            doc={doc}
            words={words}
            selected={doc.id === selected}
            onSelect={() => update({ doc: doc.id })}
          />
        ))}
        <div ref={sentinel} />
        {isFetchingNextPage && <Skeleton count={2} />}
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
  const ref = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    if (selected) ref.current?.scrollIntoView({ block: "nearest" });
  }, [selected]);
  return (
    <button
      ref={ref}
      role="listitem"
      className={selected ? `${styles.card} ${styles.selected}` : styles.card}
      onClick={onSelect}
      aria-current={selected ? "true" : undefined}
    >
      <div className={styles.cardText}>
        <span className={doc.title ? styles.cardTitle : `${styles.cardTitle} ${styles.untitled}`}>
          {highlight(name, words)}
        </span>
        <div className={styles.date}>
          {formatDate(doc.document_date) || `Imported ${formatDate(doc.ingested_at)}`}
        </div>
        {doc.snippet && <p className={styles.snippet}>{highlight(doc.snippet, words)}</p>}
        {doc.tags.length > 0 && (
          <div className={styles.tags}>
            {doc.tags.slice(0, 4).map((t) => (
              <TagChip key={t.id} tag={t} />
            ))}
            {doc.tags.length > 4 && <span className={styles.moreTags}>+{doc.tags.length - 4}</span>}
          </div>
        )}
      </div>
      <Thumbnail doc={doc} />
    </button>
  );
}

/** First page / photo for PDFs and images; a file-type tile for everything else. */
function Thumbnail({ doc }: { doc: DocumentSummary }) {
  const [failed, setFailed] = useState(false);
  const hasImage = (doc.preview === "pdf" || doc.preview === "image") && !failed;
  return (
    <span className={styles.thumb} data-kind={doc.preview}>
      {hasImage ? (
        <img src={thumbnailUrl(doc.id)} alt="" loading="lazy" decoding="async" onError={() => setFailed(true)} />
      ) : (
        <span className={styles.badge}>{fileBadge(doc.stored_name)}</span>
      )}
    </span>
  );
}

/** Marks the searched words in a piece of text. */
function highlight(text: string, words: string[]): ReactNode {
  if (!words.length) return text;
  const escaped = words.map((w) => w.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"));
  const parts = text.split(new RegExp(`(${escaped.join("|")})`, "gi"));
  return parts.map((part, i) => (i % 2 ? <mark key={i}>{part}</mark> : part));
}

function Skeleton({ count = 5 }: { count?: number }) {
  return (
    <>
      {Array.from({ length: count }, (_, i) => (
        <div key={i} className={styles.skeleton} />
      ))}
    </>
  );
}
