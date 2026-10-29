import { useMutation, useQuery, useQueryClient, type InfiniteData } from "@tanstack/react-query";
import {
  Archive,
  ArchiveRestore,
  Check,
  Download,
  ExternalLink,
  FileQuestion,
  FolderOpen,
  MoreHorizontal,
  PanelRightClose,
  PanelRightOpen,
  RefreshCw,
  RotateCcw,
  ScanText,
  Trash2,
  X,
} from "lucide-react";
import { useEffect, useMemo, useRef, useState, type ReactNode, type RefObject } from "react";
import { api, fileUrl, type DocumentDetail, type DocumentPage } from "../api/client";
import { useDocument, type DocumentFilters } from "../api/hooks";
import {
  useAddTag,
  useExtractAgain,
  useOutdate,
  useRemoveTag,
  useTaggingDone,
  useTrash,
  useUpdateDocument,
} from "../api/mutations";
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

/** The document after (or else before) this one in the list, to move on to when it leaves the list. */
function nextInList(client: ReturnType<typeof useQueryClient>, filters: DocumentFilters, id: string) {
  const pages = client.getQueryData<InfiniteData<DocumentPage>>(["documents", filters]);
  const items = pages?.pages.flatMap((p) => p.items) ?? [];
  const index = items.findIndex((d) => d.id === id);
  return index >= 0 ? (items[index + 1] ?? items[index - 1])?.id : undefined;
}

export function DocumentView() {
  const { selected, update, section, filters } = useView();
  const { data: doc, isPending, isError, error } = useDocument(selected);
  const client = useQueryClient();
  const trash = useTrash();
  /** Trash or restore; when that takes the document out of the list, move on to the next one. */
  const setTrashed = (d: DocumentDetail, trashed: boolean) => {
    const next = nextInList(client, filters, d.id); // before the list refreshes without it
    const leavesList = trashed ? section !== "trash" : section === "trash";
    trash.mutate(
      { id: d.id, trashed },
      { onSuccess: () => leavesList && update({ doc: next ?? null }, { replace: true }) },
    );
  };
  const trashKey = useRef(() => {});
  useEffect(() => {
    trashKey.current = () => doc && !doc.trashed && !trash.isPending && setTrashed(doc, true);
  });
  const [infoOpen, setInfoOpen] = useInfoOpen();
  const [mode, setMode] = useState<Mode>("preview");
  useTitle(doc ? (doc.title ?? shortName(doc.display_name)) : null);
  const tagInput = useRef<HTMLInputElement>(null);

  const titleButton = useRef<HTMLButtonElement>(null);
  const taggingButton = useRef<HTMLButtonElement>(null);

  // T: add a tag · E: edit the title · I: details on/off · O: open in the default app
  // D: tagging done / revise tagging · Delete: move to Trash
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
      } else if (event.key === "d") {
        event.preventDefault();
        taggingButton.current?.click();
      } else if (event.key === "Delete") {
        event.preventDefault();
        trashKey.current();
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
            <DateField doc={doc} field="document_date" label="Date created" />
            <span aria-hidden>·</span>
            <span>
              {fileBadge(doc.stored_name)} · {formatSize(doc.size_bytes)}
            </span>
            {doc.trashed && (
              <span className={styles.inTrash}>
                <Trash2 size={12} aria-hidden /> In Trash
              </span>
            )}
          </div>
        </div>
        <div className={styles.actions}>
          {doc.trashed ? (
            <button
              className={styles.reviseButton}
              onClick={() => setTrashed(doc, false)}
              disabled={trash.isPending}
              title="Take it out of Trash, back to where it was"
            >
              <ArchiveRestore size={14} aria-hidden /> Restore
            </button>
          ) : (
            <TaggingButton doc={doc} buttonRef={taggingButton} />
          )}
          <OsAction id={doc.id} action="open" label="Open in default app" icon={<ExternalLink size={16} />} />
          <OsAction id={doc.id} action="reveal" label="Show in folder" icon={<FolderOpen size={16} />} />
          <a className={styles.iconButton} href={fileUrl(doc.id, true)} title="Download" aria-label="Download">
            <Download size={16} />
          </a>
          <MoreMenu doc={doc} onOutdated={() => update({ doc: null })} onTrash={() => setTrashed(doc, true)} />
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

/**
 * "Done tagging" while the document is in To be tagged, "Revise tagging" afterwards. Finishing
 * from the To be tagged list moves straight on to the next document there.
 */
function TaggingButton({ doc, buttonRef }: { doc: DocumentDetail; buttonRef: RefObject<HTMLButtonElement | null> }) {
  const { section, filters, update } = useView();
  const client = useQueryClient();
  const tagging = useTaggingDone(doc.id);

  const finish = () => {
    const next = nextInList(client, filters, doc.id); // before the list refreshes without it
    tagging.mutate(true, {
      onSuccess: () => {
        if (section === "inbox") update({ doc: next ?? null }, { replace: true });
      },
    });
  };

  return doc.tagging_done ? (
    <button
      ref={buttonRef}
      className={styles.reviseButton}
      onClick={() =>
        tagging.mutate(false, {
          // it now lives in To be tagged only: continue there with it open
          onSuccess: () => update({ section: "inbox", tag: null, entity: null, related: null, q: null, sort: null }),
        })
      }
      disabled={tagging.isPending}
      title="Move back to To be tagged to rework its tags (D)"
    >
      <RotateCcw size={14} aria-hidden /> Revise tagging
    </button>
  ) : (
    <button
      ref={buttonRef}
      className={styles.doneButton}
      onClick={finish}
      disabled={tagging.isPending}
      title={
        doc.tags.length
          ? "Tagging is finished: take it out of To be tagged (D)"
          : "No tags yet. Mark it done anyway? It leaves To be tagged (D)"
      }
    >
      <Check size={15} aria-hidden /> Done tagging
    </button>
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

type DateKey = "document_date" | "sent_date" | "received_date";

/** A date of the document, edited in place; × clears it. */
function DateField({ doc, field, label }: { doc: DocumentDetail; field: DateKey; label: string }) {
  const save = useUpdateDocument(doc.id);
  const value = doc[field];
  const set = (next: string | null) =>
    field === "document_date"
      ? save.mutate(next ? { document_date: next } : { clear_document_date: true })
      : save.mutate({ [field]: next });
  return (
    <span className={styles.dateField}>
      <input
        key={`${doc.id}:${value}`}
        type="date"
        defaultValue={value ?? ""}
        aria-label={label}
        title={label}
        className={value ? undefined : styles.noDate}
        onBlur={(e) => {
          if (e.target.value && e.target.value !== value) set(e.target.value);
        }}
        onKeyDown={(e) => e.key === "Enter" && e.currentTarget.blur()}
      />
      {value && (
        <button onClick={() => set(null)} aria-label={`Remove ${label.toLowerCase()}`} title={`Remove ${label.toLowerCase()}`}>
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
          <dt>Created</dt>
          <dd>{formatDate(doc.document_date) || "—"}</dd>
          <dt>Sent</dt>
          <dd>
            <DateField doc={doc} field="sent_date" label="Date sent" />
          </dd>
          <dt>Received</dt>
          <dd>
            <DateField doc={doc} field="received_date" label="Date received" />
          </dd>
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

interface MoreMenuProps {
  doc: DocumentDetail;
  onOutdated: () => void;
  onTrash: () => void;
}

function MoreMenu({ doc, onOutdated, onTrash }: MoreMenuProps) {
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
          {!doc.trashed && (
            <button
              role="menuitem"
              onClick={() => {
                setOpen(false);
                onTrash();
              }}
            >
              <Trash2 size={15} aria-hidden />
              <span>
                Move to Trash
                <small className={styles.menuHint}>Hidden, not deleted · Delete key</small>
              </span>
            </button>
          )}
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
