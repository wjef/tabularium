import { useMutation, useQuery } from "@tanstack/react-query";
import {
  Archive,
  Download,
  ExternalLink,
  FileQuestion,
  FolderOpen,
  MoreHorizontal,
  PanelRightClose,
  PanelRightOpen,
  RefreshCw,
  ScanText,
  X,
} from "lucide-react";
import { useEffect, useMemo, useRef, useState, type ReactNode, type RefObject } from "react";
import { api, fileUrl, type DocumentDetail } from "../api/client";
import { useDocument } from "../api/hooks";
import { useAddTag, useExtractAgain, useOutdate, useRemoveTag, useUpdateDocument } from "../api/mutations";
import { EditableText } from "../components/EditableText";
import { TagChip } from "../components/TagChip";
import { TagPicker } from "../components/TagPicker";
import { toast } from "../components/toast";
import { fileBadge, formatDate, formatSize, shortName } from "../lib/format";
import { isTyping } from "../lib/keys";
import { useTitle } from "../lib/useTitle";
import styles from "./DocumentView.module.css";
import { RelatedDocuments } from "./RelatedDocuments";
import { useView } from "./useView";

const TEXT_STATUS: Record<DocumentDetail["text_status"], string> = {
  PENDING: "Waiting to be read",
  DONE: "Searchable",
  EMPTY: "No text found",
  NEEDS_OCR: "Needs OCR",
  FAILED: "Reading failed",
};

type Mode = "preview" | "text";

export function DocumentView() {
  const { selected, update } = useView();
  const { data: doc, isPending, isError, error } = useDocument(selected);
  const [infoOpen, setInfoOpen] = useInfoOpen();
  const [mode, setMode] = useState<Mode>("preview");
  useTitle(doc ? (doc.title ?? shortName(doc.display_name)) : null);
  const tagInput = useRef<HTMLInputElement>(null);

  const titleButton = useRef<HTMLButtonElement>(null);

  // T: add a tag · E: edit the title · I: details on/off · O: open in the default app
  useEffect(() => {
    if (!selected) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.ctrlKey || event.metaKey || event.altKey || isTyping(event)) return;
      if (document.querySelector("dialog[open]")) return;
      if (event.key === "t") {
        event.preventDefault();
        setInfoOpen(() => true);
        requestAnimationFrame(() => tagInput.current?.focus());
      } else if (event.key === "e") {
        event.preventDefault();
        titleButton.current?.click();
      } else if (event.key === "i") {
        setInfoOpen((v) => !v);
      } else if (event.key === "o") {
        api.post(`/documents/${selected}/open`).catch((e: Error) => toast(e.message, "error"));
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [selected, setInfoOpen]);

  if (!selected) {
    return (
      <main className={styles.empty}>
        <FileQuestion size={40} strokeWidth={1.4} aria-hidden />
        <p>Select a document to see it here.</p>
      </main>
    );
  }
  if (isPending) {
    return (
      <main className={styles.pane} aria-busy="true">
        <div className={styles.loadingHeader}>
          <span className={styles.shimmer} style={{ width: "55%", height: 26 }} />
          <span className={styles.shimmer} style={{ width: "25%", height: 14 }} />
        </div>
        <div className={styles.loadingBody} />
      </main>
    );
  }
  if (isError) {
    return (
      <main className={styles.empty}>
        <p>
          {error.message === "Document not found"
            ? "This document is no longer in the archive (it may have been outdated)."
            : `Could not load the document: ${error.message}`}
        </p>
      </main>
    );
  }

  return (
    <main className={styles.pane}>
      <header className={styles.toolbar}>
        <div className={styles.heading}>
          <DocumentTitle doc={doc} buttonRef={titleButton} />
          <div className={styles.sub}>
            <DateField doc={doc} />
            <span aria-hidden>·</span>
            <span>
              {fileBadge(doc.stored_name)} · {formatSize(doc.size_bytes)}
            </span>
          </div>
        </div>
        <div className={styles.actions}>
          <OsAction id={doc.id} action="open" label="Open in default app" icon={<ExternalLink size={16} />} />
          <OsAction id={doc.id} action="reveal" label="Show in folder" icon={<FolderOpen size={16} />} />
          <a className={styles.iconButton} href={fileUrl(doc.id, true)} title="Download" aria-label="Download">
            <Download size={16} />
          </a>
          <MoreMenu doc={doc} onOutdated={() => update({ doc: null })} />
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
        <div className={styles.main}>
          <div className={styles.modes} role="tablist" aria-label="Show">
            {(["preview", "text"] as const).map((m) => (
              <button key={m} role="tab" aria-selected={mode === m} onClick={() => setMode(m)}>
                {m === "preview" ? "Preview" : "Text"}
              </button>
            ))}
          </div>
          {mode === "preview" ? <Preview doc={doc} /> : <TextView doc={doc} />}
        </div>
        {infoOpen && <InfoPanel doc={doc} onOpenDocument={(id) => update({ doc: id })} tagInput={tagInput} />}
      </div>
    </main>
  );
}

function DocumentTitle({ doc, buttonRef }: { doc: DocumentDetail; buttonRef: RefObject<HTMLButtonElement | null> }) {
  const save = useUpdateDocument(doc.id);
  return (
    <h2 className={styles.title}>
      <EditableText
        key={doc.id}
        value={doc.title ?? ""}
        placeholder="Untitled"
        label="Title"
        buttonRef={buttonRef}
        onSave={(title) => save.mutate({ title })}
      />
    </h2>
  );
}

function DateField({ doc }: { doc: DocumentDetail }) {
  const save = useUpdateDocument(doc.id);
  return (
    <span className={styles.dateField}>
      <input
        key={doc.id}
        type="date"
        defaultValue={doc.document_date ?? ""}
        aria-label="Document date"
        title="Document date"
        className={doc.document_date ? undefined : styles.noDate}
        onBlur={(e) => {
          const value = e.target.value;
          if (value && value !== doc.document_date) save.mutate({ document_date: value });
        }}
        onKeyDown={(e) => e.key === "Enter" && e.currentTarget.blur()}
      />
      {doc.document_date && (
        <button
          onClick={() => save.mutate({ clear_document_date: true })}
          aria-label="Remove document date"
          title="Remove document date"
        >
          <X size={12} />
        </button>
      )}
    </span>
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

/** The text found in the document (text layer or OCR), as used by search. */
function TextView({ doc }: { doc: DocumentDetail }) {
  const { data, isPending } = useQuery({
    queryKey: ["document-text", doc.id, doc.text_status],
    queryFn: () => api.get<{ content: string | null }>(`/documents/${doc.id}/text`),
  });
  if (isPending) return <div className={styles.preview} />;
  return (
    <div className={styles.preview}>
      {data?.content ? (
        <pre className={styles.text}>{data.content}</pre>
      ) : (
        <div className={styles.noPreview}>
          <ScanText size={32} strokeWidth={1.4} aria-hidden />
          <p>{TEXT_STATUS[doc.text_status]}.</p>
          {doc.text.error && <p className={styles.muted}>{doc.text.error}</p>}
        </div>
      )}
    </div>
  );
}

interface InfoProps {
  doc: DocumentDetail;
  onOpenDocument: (id: string) => void;
  tagInput: RefObject<HTMLInputElement | null>;
}

function InfoPanel({ doc, onOpenDocument, tagInput }: InfoProps) {
  const save = useUpdateDocument(doc.id);
  return (
    <aside className={styles.info} aria-label="Details">
      <Section title="Tags">
        <TagsEditor doc={doc} pickerRef={tagInput} />
      </Section>

      <Section title="Note">
        <EditableText
          key={doc.id}
          value={doc.note ?? ""}
          placeholder="Add a note…"
          label="Note"
          multiline
          className={styles.note}
          onSave={(note) => save.mutate({ note })}
        />
      </Section>

      <Section title="Related documents">
        <RelatedDocuments key={doc.id} doc={doc} onOpenDocument={onOpenDocument} />
      </Section>

      <Section title="Details">
        <dl className={styles.facts}>
          <dt>Imported</dt>
          <dd>{formatDate(doc.ingested_at)}</dd>
          <dt>Keep until</dt>
          <dd>{formatDate(doc.retain_until)}</dd>
          <dt>Type</dt>
          <dd title={doc.mime_type ?? undefined}>{fileBadge(doc.stored_name)}</dd>
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

function TagsEditor({ doc, pickerRef }: { doc: DocumentDetail; pickerRef: RefObject<HTMLInputElement | null> }) {
  const add = useAddTag(doc.id);
  const remove = useRemoveTag(doc.id);
  const assigned = useMemo(() => new Set(doc.tags.map((t) => t.id)), [doc.tags]);
  return (
    <>
      {doc.tags.length > 0 && (
        <div className={styles.tags}>
          {doc.tags.map((t) => (
            <TagChip key={t.id} tag={t} full onRemove={() => remove.mutate(t.id)} />
          ))}
        </div>
      )}
      <TagPicker
        key={doc.id}
        ref={pickerRef}
        assigned={assigned}
        onPick={(choice) => add.mutate(choice)}
        placeholder={doc.tags.length ? "Add a tag…  (T)" : "Add the first tag…  (T)"}
      />
    </>
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

function MoreMenu({ doc, onOutdated }: { doc: DocumentDetail; onOutdated: () => void }) {
  const [open, setOpen] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const menu = useRef<HTMLDivElement>(null);
  const extract = useExtractAgain(doc.id);

  useEffect(() => {
    if (!open) return;
    const close = (event: MouseEvent | KeyboardEvent) => {
      if (event instanceof KeyboardEvent ? event.key === "Escape" : !menu.current?.contains(event.target as Node))
        setOpen(false);
    };
    document.addEventListener("mousedown", close);
    document.addEventListener("keydown", close);
    return () => {
      document.removeEventListener("mousedown", close);
      document.removeEventListener("keydown", close);
    };
  }, [open]);

  return (
    <div className={styles.menuWrap} ref={menu}>
      <button
        className={styles.iconButton}
        onClick={() => setOpen((v) => !v)}
        aria-label="More actions"
        title="More actions"
        aria-haspopup="menu"
        aria-expanded={open}
      >
        <MoreHorizontal size={16} />
      </button>
      {open && (
        <div className={styles.menu} role="menu">
          <button
            role="menuitem"
            onClick={() => {
              setOpen(false);
              extract.mutate();
            }}
          >
            <RefreshCw size={15} aria-hidden /> Read text again
          </button>
          <button
            role="menuitem"
            disabled={!doc.can_outdate}
            onClick={() => {
              setOpen(false);
              setConfirming(true);
            }}
          >
            <Archive size={15} aria-hidden />
            <span>
              Outdate…
              {!doc.can_outdate && (
                <small className={styles.menuHint}>Kept until {formatDate(doc.retain_until)}</small>
              )}
            </span>
          </button>
        </div>
      )}
      {confirming && <OutdateDialog doc={doc} onClose={() => setConfirming(false)} onDone={onOutdated} />}
    </div>
  );
}

function OutdateDialog({ doc, onClose, onDone }: { doc: DocumentDetail; onClose: () => void; onDone: () => void }) {
  const dialog = useRef<HTMLDialogElement>(null);
  const [reason, setReason] = useState("");
  const outdate = useOutdate(doc.id);
  useEffect(() => dialog.current?.showModal(), []);

  return (
    <dialog ref={dialog} className={styles.dialog} onClose={onClose}>
      <form
        method="dialog"
        onSubmit={(e) => {
          e.preventDefault();
          outdate.mutate(reason, {
            onSuccess: () => {
              dialog.current?.close();
              onDone();
            },
          });
        }}
      >
        <h3>Outdate this document?</h3>
        <p>
          It leaves the archive and moves to the Outdated section, where it can be restored. Nothing is
          deleted.
        </p>
        <label>
          Reason (optional)
          <textarea value={reason} onChange={(e) => setReason(e.target.value)} rows={2} autoFocus />
        </label>
        <div className={styles.dialogButtons}>
          <button type="button" className={styles.secondary} onClick={() => dialog.current?.close()}>
            Cancel
          </button>
          <button type="submit" className={styles.button} disabled={outdate.isPending}>
            Outdate
          </button>
        </div>
      </form>
    </dialog>
  );
}
