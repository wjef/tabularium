import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  CheckCircle2,
  CircleAlert,
  DatabaseBackup,
  FileJson,
  FolderSync,
  NotebookText,
  ScanText,
  ShieldCheck,
} from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router";
import {
  api,
  type CheckProblem,
  type CheckReport,
  type EvernoteReport,
  type EvernoteStatus,
  type IngestReport,
  type MaintenanceStatus,
} from "../api/client";
import { useAction } from "../api/mutations";
import { Dialog } from "../components/Dialog";
import { Card, Empty, Page } from "../components/Page";
import { formatSize } from "../lib/format";
import ui from "../styles/ui.module.css";
import styles from "./MaintenancePage.module.css";

const TEXT_LABELS: Record<string, string> = {
  DONE: "Searchable",
  PENDING: "Waiting",
  EMPTY: "No text",
  NEEDS_OCR: "Need OCR",
  FAILED: "Failed",
};

const PROBLEMS: { key: keyof Pick<CheckReport, "missing" | "changed" | "cloud_only" | "orphans">; label: string; hint: string }[] = [
  { key: "missing", label: "Missing files", hint: "In the database but not in the archive folder." },
  { key: "changed", label: "Changed files", hint: "The content no longer matches its fingerprint." },
  { key: "cloud_only", label: "Online-only files", hint: "OneDrive has not downloaded them; they could not be checked." },
  { key: "orphans", label: "Unknown files", hint: "In the archive folder but not in the database." },
];

const POLISH_DATA_COMMAND =
  "Invoke-WebRequest https://raw.githubusercontent.com/tesseract-ocr/tessdata_best/main/pol.traineddata " +
  '-OutFile "C:\\Program Files\\Tesseract-OCR\\tessdata\\pol.traineddata"';

const time = new Intl.DateTimeFormat("en-GB", { dateStyle: "medium", timeStyle: "short" });

/** Health of the archive: import, text search, integrity, backups and recovery. */
export function MaintenancePage() {
  const { data: status } = useQuery({
    queryKey: ["maintenance"],
    queryFn: () => api.get<MaintenanceStatus>("/maintenance"),
    refetchInterval: 10_000,
  });

  return (
    <Page title="Maintenance" subtitle="Keep the archive healthy. Nothing here changes or deletes your documents.">
      {!status ? (
        <Empty>Loading…</Empty>
      ) : (
        <>
          {(status.can_restore || status.outdated_restore_pending) && <RestoreCard status={status} />}
          <IngestCard />
          <EvernoteCard />
          <TextCard status={status} />
          <IntegrityCard />
          <BackupCard status={status} />
        </>
      )}
    </Page>
  );
}

function IngestCard() {
  const [report, setReport] = useState<IngestReport | null>(null);
  const scan = useAction(() => api.post<IngestReport>("/ingest/scan"), {
    invalidate: [["documents"], ["info"], ["maintenance"]],
  });
  return (
    <Card
      title={<Title icon={<FolderSync size={18} />} text="Import" />}
      actions={
        <button className={ui.secondary} disabled={scan.isPending} onClick={() => scan.mutate(undefined, { onSuccess: setReport })}>
          {scan.isPending ? "Scanning…" : "Scan ingest folder now"}
        </button>
      }
    >
      <p className={ui.muted}>New files in the ingest folder are imported automatically; scan to do it right away.</p>
      {report && (
        <>
          <p className={styles.result}>
            {report.imported.length} imported · {report.duplicates.length} already in the archive ·{" "}
            {report.failed.length} failed · {report.skipped.length} skipped
            {report.waiting.length ? ` · ${report.waiting.length} still being written` : ""}
          </p>
          {!!report.evernote?.length && (
            <p className={ui.muted}>
              {report.evernote.length} Evernote {report.evernote.length === 1 ? "file waits" : "files wait"} for Import
              from Evernote below.
            </p>
          )}
          {report.failed.length > 0 && (
            <p className={styles.error}>Could not import: {report.failed.join(", ")}</p>
          )}
        </>
      )}
    </Card>
  );
}

const plural = (n: number, one: string, many = `${one}s`) => `${n} ${n === 1 ? one : many}`;

function EvernoteCard() {
  const client = useQueryClient();
  const [confirming, setConfirming] = useState(false);
  const { data: status } = useQuery({
    queryKey: ["evernote"],
    queryFn: () => api.get<EvernoteStatus>("/ingest/evernote"),
    refetchInterval: (query) => (query.state.data?.running ? 1000 : false),
  });
  const start = useAction((dryRun: boolean) => api.post<EvernoteStatus>("/ingest/evernote", { dry_run: dryRun }));

  // When an import finishes, the new documents show up everywhere.
  const wasRunning = useRef(false);
  useEffect(() => {
    if (wasRunning.current && status && !status.running && !status.dry_run) {
      for (const key of [["documents"], ["info"], ["tags"], ["maintenance"]]) void client.invalidateQueries({ queryKey: key });
    }
    wasRunning.current = !!status?.running;
  }, [status, client]);

  if (!status) return null;
  const busy = status.running || start.isPending;
  const run = (dryRun: boolean) =>
    start.mutate(dryRun, {
      onSuccess: (s) => {
        setConfirming(false);
        client.setQueryData(["evernote"], s);
      },
    });
  return (
    <Card
      title={<Title icon={<NotebookText size={18} />} text="Import from Evernote" />}
      actions={
        <div className={ui.row}>
          <button className={ui.secondary} disabled={busy || !status.files_waiting} onClick={() => run(true)}>
            Preview
          </button>
          <button className={ui.button} disabled={busy || !status.files_waiting} onClick={() => setConfirming(true)}>
            Import
          </button>
        </div>
      }
    >
      <p className={ui.muted}>
        Put Evernote exports (.enex files) in the ingest folder. The photos of a note become one PDF, a note without
        attachments is printed to PDF, and other attachments are kept as they are. Everything arrives in To be tagged,
        with the notebook, Evernote tags and the note's text in the document's note.
      </p>
      {!status.running && (
        <p className={styles.result}>
          {status.files_waiting
            ? `${plural(status.files_waiting, "Evernote file")} in the ingest folder.`
            : "No Evernote files in the ingest folder."}
        </p>
      )}
      {!status.printer_found && (
        <p className={styles.status}>
          <span className={`${ui.pill} ${ui.warn}`}>
            <CircleAlert size={12} /> Microsoft Edge not found
          </span>{" "}
          <span className={ui.muted}>Notes without attachments cannot be printed to PDF; they will be reported as failed.</span>
        </p>
      )}
      {status.running && (
        <div className={styles.progress}>
          <p className={styles.result}>
            {status.dry_run ? "Previewing" : "Importing"}… file {Math.min(status.files_done + 1, status.files_total)} of{" "}
            {status.files_total} · {plural(status.notes_done, "note")} so far
          </p>
          <div className={styles.bar}>
            <span style={{ width: `${status.files_total ? (100 * status.files_done) / status.files_total : 0}%` }} />
          </div>
        </div>
      )}
      {status.error && <p className={styles.error}>{status.error}</p>}
      {!status.running && status.report && <EvernoteResult report={status.report} />}
      {confirming && (
        <Dialog
          title="Import from Evernote?"
          confirmLabel="Import"
          busy={start.isPending}
          onClose={() => setConfirming(false)}
          onConfirm={() => run(false)}
        >
          <p>
            {plural(status.files_waiting, "Evernote file")} will be imported into To be tagged. The .enex files move to
            _processed afterwards; importing the same notes again later adds nothing twice.
          </p>
        </Dialog>
      )}
    </Card>
  );
}

function EvernoteResult({ report: r }: { report: EvernoteReport }) {
  const kinds = [
    r.photo_pdfs && plural(r.photo_pdfs, "photo PDF"),
    r.printed && plural(r.printed, "printed note"),
    r.attachments && plural(r.attachments, "attachment"),
  ].filter(Boolean);
  const other = [
    r.already_imported && `${plural(r.already_imported, "note")} imported before`,
    r.duplicates && `${plural(r.duplicates, "file")} already in the archive`,
    r.empty && `${plural(r.empty, "empty note")} skipped`,
    r.waiting.length && `${plural(r.waiting.length, "file")} still being written`,
  ].filter(Boolean);
  return (
    <>
      <p className={styles.result}>
        <strong>
          {r.dry_run ? "Preview: " : "Done: "}
          {plural(r.documents, "document")} {r.dry_run ? "would be created" : "created"}
        </strong>{" "}
        from {plural(r.notes, "note")} in {plural(r.files, "file")}
        {kinds.length ? ` (${kinds.join(", ")})` : ""}.
      </p>
      {other.length > 0 && <p className={ui.muted}>{other.join(" · ")}.</p>}
      {r.failed.length > 0 && (
        <div className={styles.problems}>
          <h3>Could not import ({r.failed.length})</h3>
          <ul>
            {r.failed.slice(0, 50).map((f, i) => (
              <li key={i} className={styles.error}>
                {f}
              </li>
            ))}
          </ul>
          {r.failed.length > 50 && <p className={ui.muted}>…and {r.failed.length - 50} more (see the log).</p>}
        </div>
      )}
    </>
  );
}

function TextCard({ status }: { status: MaintenanceStatus }) {
  const ocrCheck = useAction(() => api.post<{ message: string }>("/maintenance/ocr-check"), {
    invalidate: [["maintenance"]],
    success: (r) => (r.message === "available" ? "OCR is available." : "OCR is still not available."),
  });
  const { ocr } = status;
  return (
    <Card
      title={<Title icon={<ScanText size={18} />} text="Text search and OCR" />}
      actions={
        <button className={ui.secondary} disabled={ocrCheck.isPending} onClick={() => ocrCheck.mutate()}>
          Check OCR again
        </button>
      }
    >
      <div className={styles.counts}>
        {Object.entries(TEXT_LABELS).map(([key, label]) => (
          <div key={key} className={styles.count}>
            <strong>{status.text_counts[key] ?? 0}</strong>
            <span>{label}</span>
          </div>
        ))}
      </div>
      <p className={styles.status}>
        {ocr.enabled && ocr.available ? (
          <span className={`${ui.pill} ${ui.ok}`}>
            <CheckCircle2 size={12} /> OCR ready
          </span>
        ) : (
          <span className={`${ui.pill} ${ui.warn}`}>
            <CircleAlert size={12} /> {ocr.enabled ? "OCR not available" : "OCR turned off"}
          </span>
        )}{" "}
        <span className={ui.muted}>
          Languages {ocr.languages}. {ocr.message}
        </span>
      </p>
      {ocr.enabled && !ocr.available && (
        <div className={styles.howto}>
          <p>
            To read scans and photos: install Tesseract, add the Polish language file, then click “Check OCR again”
            (no restart needed).
          </p>
          {!ocr.command && (
            <>
              <p>1. Install Tesseract (PowerShell):</p>
              <pre>winget install --id UB-Mannheim.TesseractOCR</pre>
            </>
          )}
          <p>Add the Polish language file, in PowerShell started with “Run as administrator” (winget installs English only):</p>
          <pre>{POLISH_DATA_COMMAND}</pre>
        </div>
      )}
    </Card>
  );
}

function IntegrityCard() {
  const navigate = useNavigate();
  const [report, setReport] = useState<CheckReport | null>(null);
  const check = useAction((full: boolean) => api.post<CheckReport>(`/maintenance/check${full ? "?full=true" : ""}`));
  const run = (full: boolean) => check.mutate(full, { onSuccess: setReport });
  return (
    <Card
      title={<Title icon={<ShieldCheck size={18} />} text="Integrity check" />}
      actions={
        <div className={ui.row}>
          <button className={ui.secondary} disabled={check.isPending} onClick={() => run(false)}>
            Quick check
          </button>
          <button
            className={ui.secondary}
            disabled={check.isPending}
            onClick={() => run(true)}
            title="Reads every file and compares its fingerprint; can take a while"
          >
            Full check
          </button>
        </div>
      }
    >
      <p className={ui.muted}>
        The quick check looks for missing and unknown files; the full check also reads every file to find changed ones.
      </p>
      {check.isPending && <p className={styles.result}>Checking…</p>}
      {report && !check.isPending && (
        <>
          <p className={styles.result}>
            {report.healthy ? (
              <span className={`${ui.pill} ${ui.ok}`}>
                <CheckCircle2 size={12} /> All good
              </span>
            ) : (
              <span className={`${ui.pill} ${ui.bad}`}>
                <CircleAlert size={12} /> Problems found
              </span>
            )}{" "}
            {report.ok} of {report.checked} files OK ({report.full ? "full" : "quick"} check)
          </p>
          {PROBLEMS.filter((p) => report[p.key].length).map((p) => (
            <div key={p.key} className={styles.problems}>
              <h3>
                {p.label} ({report[p.key].length})
              </h3>
              <p className={ui.muted}>{p.hint}</p>
              <ul>
                {report[p.key].map((item: CheckProblem) => (
                  <li key={item.path}>
                    {item.document_id ? (
                      <button className={styles.link} onClick={() => navigate(`/?doc=${item.document_id}`)}>
                        {item.name ?? item.path}
                      </button>
                    ) : (
                      <span className={ui.mono}>{item.path}</span>
                    )}
                    {item.detail && <span className={ui.muted}> — {item.detail}</span>}
                  </li>
                ))}
              </ul>
            </div>
          ))}
        </>
      )}
    </Card>
  );
}

function BackupCard({ status }: { status: MaintenanceStatus }) {
  const backup = useAction(() => api.post<{ message: string }>("/maintenance/backup"), {
    invalidate: [["maintenance"]],
    success: (r) => `Backup saved: ${r.message}`,
  });
  const metadata = useAction(() => api.post<{ message: string }>("/maintenance/metadata"), {
    invalidate: [["maintenance"]],
    success: (r) => r.message,
  });
  const { backups, metadata: meta } = status;
  return (
    <Card
      title={<Title icon={<DatabaseBackup size={18} />} text="Backups" />}
      actions={
        <button className={ui.secondary} disabled={backup.isPending} onClick={() => backup.mutate()}>
          Back up now
        </button>
      }
    >
      <p className={ui.muted}>
        The database is backed up at every start; the newest {backups.keep} backups are kept in{" "}
        <span className={ui.mono}>{backups.dir}</span>.
      </p>
      {backups.files.length ? (
        <ul className={styles.files}>
          {backups.files.slice(0, 5).map((f) => (
            <li key={f.name}>
              <span className={ui.mono}>{f.name}</span>
              <span className={ui.muted}>{formatSize(f.size)}</span>
            </li>
          ))}
        </ul>
      ) : (
        <p className={ui.muted}>No backups yet.</p>
      )}
      <div className={styles.metadata}>
        <div>
          <Title icon={<FileJson size={16} />} text="metadata.json" />
          <p className={ui.muted}>
            A copy of all titles, tags and entities next to the files, so the archive can be rebuilt without the database.
            {meta.last_written ? ` Last written ${time.format(new Date(meta.last_written))}.` : " Not written yet."}
          </p>
          {meta.last_error && <p className={styles.error}>{meta.last_error}</p>}
        </div>
        <button className={ui.secondary} disabled={metadata.isPending} onClick={() => metadata.mutate()}>
          Write now
        </button>
      </div>
    </Card>
  );
}

function RestoreCard({ status }: { status: MaintenanceStatus }) {
  const [confirming, setConfirming] = useState(false);
  const invalidate = [["maintenance"], ["documents"], ["tags"], ["entities"], ["info"], ["outdated"]];
  const restore = useAction(() => api.post<{ message: string }>("/maintenance/restore"), {
    invalidate,
    success: (r) => r.message,
  });
  const restoreOutdated = useAction(() => api.post<{ message: string }>("/maintenance/restore-outdated"), {
    invalidate,
    success: (r) => `${r.message} outdated documents restored.`,
  });
  return (
    <Card title={<Title icon={<CircleAlert size={18} />} text="Recovery" />}>
      {status.can_restore && (
        <div className={styles.metadata}>
          <p>
            The database is empty, but <span className={ui.mono}>metadata.json</span> was found in the archive. Rebuild
            the database from it?
          </p>
          <button className={ui.button} onClick={() => setConfirming(true)}>
            Restore database
          </button>
        </div>
      )}
      {status.outdated_restore_pending && (
        <div className={styles.metadata}>
          <p>The outdated documents' list can be rebuilt from the Outdated folder's metadata.</p>
          <button className={ui.button} disabled={restoreOutdated.isPending} onClick={() => restoreOutdated.mutate()}>
            Restore outdated list
          </button>
        </div>
      )}
      {confirming && (
        <Dialog
          title="Restore the database from metadata.json?"
          confirmLabel="Restore"
          busy={restore.isPending}
          onClose={() => setConfirming(false)}
          onConfirm={() => restore.mutate(undefined, { onSuccess: () => setConfirming(false) })}
        >
          <p>Titles, tags, entities, links and history are read back from the copy next to your files.</p>
        </Dialog>
      )}
    </Card>
  );
}

function Title({ icon, text }: { icon: React.ReactNode; text: string }) {
  return (
    <span className={styles.title}>
      {icon}
      {text}
    </span>
  );
}
