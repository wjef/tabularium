import { useQuery } from "@tanstack/react-query";
import { ArchiveRestore, Download, Search } from "lucide-react";
import { useMemo, useState } from "react";
import { useNavigate, useSearchParams } from "react-router";
import { api, outdatedFileUrl, type OutdatedDetail, type OutdatedSummary } from "../api/client";
import { useAction } from "../api/mutations";
import { Dialog } from "../components/Dialog";
import { Empty, Page } from "../components/Page";
import { formatDate, shortName } from "../lib/format";
import { matchScore } from "../lib/text";
import ui from "../styles/ui.module.css";
import styles from "./OutdatedPage.module.css";

const INLINE = /^(application\/pdf|text\/plain|image\/)/;

/** Documents taken out of the archive; they can be looked at and restored, never deleted. */
export function OutdatedPage() {
  const [params, setParams] = useSearchParams();
  const selected = params.get("id");
  const [filter, setFilter] = useState("");
  const { data, isPending } = useQuery({
    queryKey: ["outdated"],
    queryFn: () => api.get<OutdatedSummary[]>("/outdated"),
  });
  const rows = useMemo(
    () =>
      (data ?? []).filter(
        (r) => !filter.trim() || matchScore([r.display_name, r.reason ?? "", ...r.tag_paths].join(" "), filter) > 0,
      ),
    [data, filter],
  );

  return (
    <Page
      title="Outdated"
      subtitle="Documents past their retention date that were taken out of the archive. Nothing here is deleted."
      wide
    >
      <div className={styles.list}>
        <label className={styles.filter}>
          <Search size={14} aria-hidden />
          <input placeholder="Filter" value={filter} onChange={(e) => setFilter(e.target.value)} aria-label="Filter" />
        </label>
        {isPending && <Empty>Loading…</Empty>}
        {data && rows.length === 0 && <Empty>{data.length ? "Nothing matches." : "No outdated documents."}</Empty>}
        {rows.map((r) => (
          <button
            key={r.id}
            className={r.id === selected ? `${styles.item} ${styles.selected}` : styles.item}
            onClick={() => setParams({ id: r.id })}
          >
            <span className={styles.name}>{shortName(r.display_name)}</span>
            <span className={styles.meta}>
              Outdated {formatDate(r.outdated_at)}
              {r.document_date && ` · dated ${formatDate(r.document_date)}`}
            </span>
            {r.reason && <span className={styles.reason}>{r.reason}</span>}
          </button>
        ))}
      </div>
      <div className={styles.detail}>
        {selected ? <OutdatedView key={selected} id={selected} /> : <Empty>Select a document to see it here.</Empty>}
      </div>
    </Page>
  );
}

function OutdatedView({ id }: { id: string }) {
  const navigate = useNavigate();
  const [confirming, setConfirming] = useState(false);
  const { data: doc, isError, error } = useQuery({
    queryKey: ["outdated", id],
    queryFn: () => api.get<OutdatedDetail>(`/outdated/${id}`),
  });
  const restore = useAction(() => api.post<{ message: string }>(`/outdated/${id}/restore`), {
    invalidate: [["outdated"], ["documents"], ["info"], ["tags"]],
    success: (r) => r.message,
  });

  if (isError) return <Empty>{error.message}</Empty>;
  if (!doc) return null;
  const inline = doc.mime_type && INLINE.test(doc.mime_type);
  return (
    <div className={styles.view}>
      <header className={styles.viewHeader}>
        <div>
          <h2>{doc.title ?? shortName(doc.display_name)}</h2>
          <p className={ui.muted}>
            Outdated {formatDate(doc.outdated_at)} by {doc.outdated_by}
            {doc.reason && <> · “{doc.reason}”</>}
          </p>
        </div>
        <div className={ui.row}>
          <a className={ui.secondary} href={`${outdatedFileUrl(id)}?download=1`}>
            <Download size={15} aria-hidden /> Download
          </a>
          <button className={ui.button} onClick={() => setConfirming(true)}>
            <ArchiveRestore size={15} aria-hidden /> Restore
          </button>
        </div>
      </header>
      <div className={styles.viewBody}>
        <div className={styles.preview}>
          {inline ? (
            doc.mime_type!.startsWith("image/") ? (
              <img src={outdatedFileUrl(id)} alt="" />
            ) : (
              <iframe src={outdatedFileUrl(id)} title="Preview" />
            )
          ) : (
            <Empty>This file type can't be shown in the browser.</Empty>
          )}
        </div>
        <dl className={styles.facts}>
          <dt>Document date</dt>
          <dd>{formatDate(doc.document_date) || "—"}</dd>
          <dt>Kept until</dt>
          <dd>{formatDate(doc.retain_until)}</dd>
          <dt>Tags</dt>
          <dd>
            {doc.tag_paths.length ? (
              <span className={styles.tags}>
                {doc.tag_paths.map((p) => (
                  <span key={p} className={ui.pill}>
                    {p}
                  </span>
                ))}
              </span>
            ) : (
              "—"
            )}
          </dd>
          {doc.note && (
            <>
              <dt>Note</dt>
              <dd className={styles.note}>{doc.note}</dd>
            </>
          )}
          <dt>History</dt>
          <dd>
            <ul className={styles.events}>
              {doc.events.map((e, i) => (
                <li key={i}>
                  {formatDate(e.at)} · {e.action.toLowerCase()} · {e.actor}
                </li>
              ))}
            </ul>
          </dd>
        </dl>
      </div>
      {confirming && (
        <Dialog
          title="Restore this document?"
          confirmLabel="Restore"
          busy={restore.isPending}
          onClose={() => setConfirming(false)}
          onConfirm={() =>
            restore.mutate(undefined, {
              onSuccess: () => navigate({ pathname: "/", search: `?doc=${id}` }),
            })
          }
        >
          <p>It goes back into the archive with its tags, and opens in the document list.</p>
        </Dialog>
      )}
    </div>
  );
}
