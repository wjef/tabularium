import { Link2, Plus, X } from "lucide-react";
import { useEffect, useId, useState, type KeyboardEvent } from "react";
import type { DocumentDetail } from "../api/client";
import { useDocumentSearch, useLinkChoices } from "../api/hooks";
import { useAddLink, useRemoveLink } from "../api/mutations";
import { formatDate, shortName } from "../lib/format";
import styles from "./RelatedDocuments.module.css";

interface Props {
  doc: DocumentDetail;
  onOpenDocument: (id: string) => void;
}

/** Links to other documents (invoice ↔ payment, corrections, …), added and removed in place. */
export function RelatedDocuments({ doc, onOpenDocument }: Props) {
  const [adding, setAdding] = useState(false);
  const remove = useRemoveLink(doc.id);

  return (
    <>
      {doc.links.length > 0 && (
        <ul className={styles.links}>
          {doc.links.map((link) => (
            <li key={link.relation_id}>
              <span className={styles.phrase}>{link.phrase}</span>
              <span className={styles.row}>
                <button className={styles.linkButton} onClick={() => onOpenDocument(link.other.id)}>
                  <Link2 size={13} aria-hidden />
                  <span>{shortName(link.other.display_name)}</span>
                </button>
                <button
                  className={styles.remove}
                  onClick={() => remove.mutate({ relationId: link.relation_id, otherId: link.other.id })}
                  aria-label={`Remove link to ${link.other.display_name}`}
                  title="Remove link"
                >
                  <X size={13} />
                </button>
              </span>
            </li>
          ))}
        </ul>
      )}
      {adding ? (
        <LinkForm doc={doc} onDone={() => setAdding(false)} />
      ) : (
        <button className={styles.add} onClick={() => setAdding(true)}>
          <Plus size={14} aria-hidden /> Link a document
        </button>
      )}
    </>
  );
}

function LinkForm({ doc, onDone }: { doc: DocumentDetail; onDone: () => void }) {
  const choices = useLinkChoices().data ?? [];
  const [kind, setKind] = useState("RELATED:out");
  const [query, setQuery] = useState("");
  const [debounced, setDebounced] = useState("");
  const [active, setActive] = useState(0);
  const add = useAddLink(doc.id);
  const listId = useId();

  useEffect(() => {
    const timer = setTimeout(() => setDebounced(query), 200);
    return () => clearTimeout(timer);
  }, [query]);
  const linked = new Set([doc.id, ...doc.links.map((l) => l.other.id)]);
  const results = (useDocumentSearch(debounced, "any").data?.items ?? []).filter((d) => !linked.has(d.id));
  const current = Math.min(active, results.length - 1);

  const pick = (otherId: string) => add.mutate({ otherId, kind }, { onSuccess: onDone });
  const onKeyDown = (event: KeyboardEvent) => {
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      const step = event.key === "ArrowDown" ? 1 : -1;
      setActive((current + step + results.length) % Math.max(results.length, 1));
    } else if (event.key === "Enter" && results[current]) {
      event.preventDefault();
      pick(results[current].id);
    } else if (event.key === "Escape") {
      event.stopPropagation();
      onDone();
    }
  };

  return (
    <div className={styles.form}>
      <label className={styles.kind}>
        <span>This document</span>
        <select value={kind} onChange={(e) => setKind(e.target.value)} aria-label="Relation">
          {choices.map((c) => (
            <option key={c.value} value={c.value}>
              {c.phrase}
            </option>
          ))}
        </select>
      </label>
      <input
        className={styles.search}
        autoFocus
        placeholder="Find the other document…"
        value={query}
        onChange={(e) => {
          setQuery(e.target.value);
          setActive(0);
        }}
        onKeyDown={onKeyDown}
        role="combobox"
        aria-expanded={results.length > 0}
        aria-controls={listId}
        aria-label="Find the other document"
      />
      {results.length > 0 && (
        <ul id={listId} role="listbox" className={styles.results}>
          {results.map((d, i) => (
            <li
              key={d.id}
              role="option"
              aria-selected={i === current}
              className={i === current ? styles.active : undefined}
              onMouseEnter={() => setActive(i)}
              onClick={() => pick(d.id)}
            >
              <span className={styles.resultTitle}>{d.title ?? shortName(d.display_name)}</span>
              <span className={styles.resultDate}>{formatDate(d.document_date)}</span>
            </li>
          ))}
        </ul>
      )}
      {debounced && results.length === 0 && <p className={styles.none}>No other documents match.</p>}
      <button className={styles.cancel} onClick={onDone}>
        Cancel
      </button>
    </div>
  );
}
