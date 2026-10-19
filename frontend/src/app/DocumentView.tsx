import { useMutation } from "@tanstack/react-query";
import {
  Download,
  ExternalLink,
  FileQuestion,
  FolderOpen,
  Link2,
  PanelRightClose,
  PanelRightOpen,
  ScanText,
} from "lucide-react";
import { useState, type ReactNode } from "react";
import { api, fileUrl, type DocumentDetail } from "../api/client";
import { useDocument } from "../api/hooks";
import { TagChip } from "../components/TagChip";
import { fileBadge, formatDate, formatSize, shortName } from "../lib/format";
import styles from "./DocumentView.module.css";
import { useView } from "./useView";

const TEXT_STATUS: Record<DocumentDetail["text_status"], string> = {
  PENDING: "Waiting to be read",
  DONE: "Searchable",
  EMPTY: "No text found",
  NEEDS_OCR: "Needs OCR",
  FAILED: "Reading failed",
};

export function DocumentView() {
  const { selected, update } = useView();
  const { data: doc, isPending, isError, error } = useDocument(selected);
  const [infoOpen, setInfoOpen] = useInfoOpen();

  if (!selected) {
    return (
      <main className={styles.empty}>
        <FileQuestion size={40} strokeWidth={1.4} aria-hidden />
        <p>Select a document to see it here.</p>
      </main>
    );
  }
  if (isPending) return <main className={styles.pane} aria-busy="true" />;
  if (isError) {
    return (
      <main className={styles.empty}>
        <p>Could not load the document: {error.message}</p>
      </main>
    );
  }

  const name = doc.title ?? shortName(doc.display_name);
  return (
    <main className={styles.pane}>
      <header className={styles.toolbar}>
        <div className={styles.heading}>
          <h2 className={doc.title ? styles.title : `${styles.title} ${styles.untitled}`} title={doc.display_name}>
            {name}
          </h2>
          <div className={styles.sub}>
            {doc.document_date ? formatDate(doc.document_date) : "No document date"}
            <span aria-hidden>·</span>
            {fileBadge(doc.stored_name)} · {formatSize(doc.size_bytes)}
          </div>
        </div>
        <div className={styles.actions}>
          <OsAction id={doc.id} action="open" label="Open in default app" icon={<ExternalLink size={16} />} />
          <OsAction id={doc.id} action="reveal" label="Show in folder" icon={<FolderOpen size={16} />} />
          <a className={styles.iconButton} href={fileUrl(doc.id, true)} title="Download" aria-label="Download">
            <Download size={16} />
          </a>
          <button
            className={styles.iconButton}
            onClick={() => setInfoOpen((v) => !v)}
            title={infoOpen ? "Hide details" : "Show details"}
            aria-label={infoOpen ? "Hide details" : "Show details"}
            aria-pressed={infoOpen}
          >
            {infoOpen ? <PanelRightClose size={16} /> : <PanelRightOpen size={16} />}
          </button>
        </div>
      </header>

      <div className={infoOpen ? styles.body : `${styles.body} ${styles.noInfo}`}>
        <Preview doc={doc} />
        {infoOpen && <InfoPanel doc={doc} onOpenDocument={(id) => update({ doc: id })} />}
      </div>
    </main>
  );
}

const INFO_KEY = "tabularium.info";

/** Details panel: open by default on wide screens, then as the user last left it. */
function useInfoOpen(): [boolean, (update: (open: boolean) => boolean) => void] {
  const [open, setOpen] = useState(() => {
    try {
      const saved = localStorage.getItem(INFO_KEY);
      if (saved) return saved === "1";
    } catch {
      /* storage blocked */
    }
    return window.innerWidth >= 1280;
  });
  const toggle = (update: (open: boolean) => boolean) =>
    setOpen((prev) => {
      const next = update(prev);
      try {
        localStorage.setItem(INFO_KEY, next ? "1" : "0");
      } catch {
        /* storage blocked */
      }
      return next;
    });
  return [open, toggle];
}

function Preview({ doc }: { doc: DocumentDetail }) {
  const src = fileUrl(doc.id);
  if (doc.preview === "image") {
    return (
      <div className={styles.preview}>
        <img className={styles.image} src={src} alt={doc.title ?? "Document image"} />
      </div>
    );
  }
  if (doc.preview === "pdf" || doc.preview === "text") {
    return (
      <div className={styles.preview}>
        <iframe key={doc.id} className={styles.frame} src={src} title="Document preview" />
      </div>
    );
  }
  return (
    <div className={`${styles.preview} ${styles.noPreview}`}>
      <span className={styles.bigBadge}>{fileBadge(doc.stored_name)}</span>
      <p>This file type can't be shown in the browser.</p>
      <a className={styles.button} href={fileUrl(doc.id, true)}>
        <Download size={15} /> Download
      </a>
    </div>
  );
}

function InfoPanel({ doc, onOpenDocument }: { doc: DocumentDetail; onOpenDocument: (id: string) => void }) {
  return (
    <aside className={styles.info} aria-label="Details">
      <Section title="Tags">
        {doc.tags.length ? (
          <div className={styles.tags}>
            {doc.tags.map((t) => (
              <TagChip key={t.id} tag={t} full />
            ))}
          </div>
        ) : (
          <p className={styles.muted}>No tags yet.</p>
        )}
      </Section>

      {doc.note && (
        <Section title="Note">
          <p className={styles.note}>{doc.note}</p>
        </Section>
      )}

      <Section title="Related documents">
        {doc.links.length ? (
          <ul className={styles.links}>
            {doc.links.map((link) => (
              <li key={link.relation_id}>
                <span className={styles.phrase}>{link.phrase}</span>
                <button className={styles.linkButton} onClick={() => onOpenDocument(link.other.id)}>
                  <Link2 size={13} aria-hidden />
                  <span>{shortName(link.other.display_name)}</span>
                </button>
              </li>
            ))}
          </ul>
        ) : (
          <p className={styles.muted}>None.</p>
        )}
      </Section>

      <Section title="Details">
        <dl className={styles.facts}>
          <dt>Document date</dt>
          <dd>{formatDate(doc.document_date) || "—"}</dd>
          <dt>Imported</dt>
          <dd>{formatDate(doc.ingested_at)}</dd>
          <dt>Keep until</dt>
          <dd>{formatDate(doc.retain_until)}</dd>
          <dt>Type</dt>
          <dd>{doc.mime_type ?? fileBadge(doc.stored_name)}</dd>
          <dt>Size</dt>
          <dd>{formatSize(doc.size_bytes)}</dd>
          <dt>Text</dt>
          <dd>
            <span className={styles.status} data-status={doc.text_status}>
              <ScanText size={13} aria-hidden /> {TEXT_STATUS[doc.text_status]}
            </span>
            {doc.text.pages ? (
              <span className={styles.muted}>
                {" "}
                · {doc.text.pages} {doc.text.pages === 1 ? "page" : "pages"}
              </span>
            ) : null}
          </dd>
          <dt>File</dt>
          <dd className={styles.mono} title={doc.stored_name}>
            {shortName(doc.stored_name)}
          </dd>
        </dl>
      </Section>
    </aside>
  );
}

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className={styles.section}>
      <h3>{title}</h3>
      {children}
    </section>
  );
}

function OsAction({ id, action, label, icon }: { id: string; action: "open" | "reveal"; label: string; icon: ReactNode }) {
  const mutation = useMutation({ mutationFn: () => api.post(`/documents/${id}/${action}`) });
  return (
    <button
      className={styles.iconButton}
      onClick={() => mutation.mutate()}
      disabled={mutation.isPending}
      title={mutation.isError ? mutation.error.message : label}
      aria-label={label}
    >
      {icon}
    </button>
  );
}
