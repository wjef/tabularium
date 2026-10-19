import { Building2, FileText, GitMerge, MoveRight, Plus, Search, Tag as TagIcon, Trash2 } from "lucide-react";
import { useMemo, useState } from "react";
import { useNavigate, useSearchParams } from "react-router";
import { api, type TagNode } from "../api/client";
import { useTags } from "../api/hooks";
import { useAction } from "../api/mutations";
import { Dialog } from "../components/Dialog";
import { EditableText } from "../components/EditableText";
import { Card, Empty, Page } from "../components/Page";
import { TagPicker } from "../components/TagPicker";
import { matchScore } from "../lib/text";
import ui from "../styles/ui.module.css";
import styles from "./TagsPage.module.css";

const TAG_QUERIES = [["tags"], ["documents"], ["document"], ["info"]];

/** Tags in tree order (parents before their children), with their depth. */
function treeOrder(tags: TagNode[]): { tag: TagNode; depth: number }[] {
  const children = new Map<string | null, TagNode[]>();
  for (const t of tags) children.set(t.parent_id, [...(children.get(t.parent_id) ?? []), t]);
  const out: { tag: TagNode; depth: number }[] = [];
  const walk = (parent: string | null, depth: number) => {
    const list = (children.get(parent) ?? []).sort(
      (a, b) => a.sort_order - b.sort_order || a.name.localeCompare(b.name),
    );
    for (const tag of list) {
      out.push({ tag, depth });
      walk(tag.id, depth + 1);
    }
  };
  walk(null, 0);
  return out;
}

export function TagsPage() {
  const tags = useTags().data;
  const [params, setParams] = useSearchParams();
  const selectedId = params.get("id");
  const [filter, setFilter] = useState("");
  const [creating, setCreating] = useState(false);
  const rows = useMemo(() => treeOrder(tags ?? []), [tags]);
  const visible = filter.trim() ? rows.filter((r) => matchScore(r.tag.path, filter) > 0) : rows;
  const selected = tags?.find((t) => t.id === selectedId);
  const hasChildren = useMemo(() => new Set((tags ?? []).map((t) => t.parent_id)), [tags]);

  return (
    <Page
      title="Tags"
      subtitle="Rename, move, merge and tidy up the tag tree."
      wide
      actions={
        <button className={ui.button} onClick={() => setCreating(true)}>
          <Plus size={15} aria-hidden /> New tag
        </button>
      }
    >
      <div className={styles.tree}>
        <label className={styles.filter}>
          <Search size={14} aria-hidden />
          <input placeholder="Filter tags" value={filter} onChange={(e) => setFilter(e.target.value)} aria-label="Filter tags" />
        </label>
        {tags && !visible.length && <Empty>{tags.length ? "No tag matches." : "No tags yet."}</Empty>}
        <ul>
          {visible.map(({ tag, depth }) => (
            <li key={tag.id}>
              <button
                className={tag.id === selectedId ? `${styles.node} ${styles.selected}` : styles.node}
                style={{ paddingLeft: 10 + (filter.trim() ? 0 : depth * 18) }}
                onClick={() => setParams({ id: tag.id })}
              >
                {tag.entity_id ? <Building2 size={14} aria-hidden /> : <TagIcon size={14} aria-hidden />}
                <span className={styles.label}>{filter.trim() ? tag.path : tag.name}</span>
                {tag.count > 0 && <span className={styles.count}>{tag.count}</span>}
              </button>
            </li>
          ))}
        </ul>
      </div>
      <div className={styles.detail}>
        {selected ? (
          <TagDetail key={selected.id} tag={selected} hasChildren={hasChildren.has(selected.id)} onGone={() => setParams({})} />
        ) : (
          <Empty>Select a tag to change it.</Empty>
        )}
      </div>
      {creating && <NewTagDialog onClose={() => setCreating(false)} onCreated={(id) => setParams({ id })} />}
    </Page>
  );
}

function TagDetail({ tag, hasChildren, onGone }: { tag: TagNode; hasChildren: boolean; onGone: () => void }) {
  const navigate = useNavigate();
  const [merging, setMerging] = useState<TagNode | null>(null);
  const [deleting, setDeleting] = useState(false);
  const parentPath = tag.path.split(" / ").slice(0, -1).join(" / ");
  const self = useMemo(() => new Set([tag.id]), [tag.id]);

  const rename = useAction((name: string) => api.patch<{ message: string }>(`/tags/${tag.id}`, { name }), {
    invalidate: TAG_QUERIES,
    success: () => "Tag renamed.",
  });
  const move = useAction(
    (parent: string | null) =>
      api.patch<{ message: string }>(`/tags/${tag.id}`, parent ? { parent_id: parent } : { move_to_top: true }),
    { invalidate: TAG_QUERIES, success: () => "Tag moved." },
  );
  const merge = useAction((target: string) => api.post<{ message: string }>(`/tags/${tag.id}/merge`, { target_id: target }), {
    invalidate: TAG_QUERIES,
    success: (r) => `Tag merged; ${r.message} document assignments moved.`,
  });
  const remove = useAction(() => api.delete<{ message: string }>(`/tags/${tag.id}`), {
    invalidate: TAG_QUERIES,
    success: (r) => r.message,
  });

  return (
    <div className={styles.detailBody}>
      {parentPath && <div className={styles.crumb}>{parentPath} /</div>}
      <h2 className={styles.name}>
        {tag.entity_id ? (
          tag.name
        ) : (
          <EditableText value={tag.name} placeholder="Name" label="Tag name" onSave={(name) => rename.mutate(name)} />
        )}
      </h2>
      <div className={`${ui.row} ${styles.actionsRow}`}>
        <span className={ui.pill}>
          {tag.count} {tag.count === 1 ? "document" : "documents"}
        </span>
        {tag.entity_id && (
          <button className={ui.ghost} onClick={() => navigate(`/entities?id=${tag.entity_id}`)}>
            <Building2 size={14} aria-hidden /> Entity tag — rename it on the entity
          </button>
        )}
        <button className={ui.ghost} onClick={() => navigate({ pathname: "/", search: `?tag=${tag.id}` })}>
          <FileText size={14} aria-hidden /> Show documents
        </button>
      </div>

      <Card title="Move">
        <p className={ui.muted}>Put this tag under another tag, or make it a top-level tag. Documents keep it.</p>
        <div className={styles.pickRow}>
          <MoveRight size={16} aria-hidden className={ui.muted} />
          <div className={styles.grow}>
            <TagPicker
              existingOnly
              assigned={self}
              label="New parent tag"
              placeholder="New parent tag…"
              onPick={(c) => c.kind === "tag" && move.mutate(c.tag.id)}
            />
          </div>
          {parentPath && (
            <button className={ui.secondary} onClick={() => move.mutate(null)}>
              Make top level
            </button>
          )}
        </div>
      </Card>

      <Card title="Merge">
        <p className={ui.muted}>
          Move all documents (and sub-tags) of this tag to another tag, then remove this one. Useful for duplicates
          like “invoice” and “invoices”.
        </p>
        <div className={styles.pickRow}>
          <GitMerge size={16} aria-hidden className={ui.muted} />
          <div className={styles.grow}>
            <TagPicker
              existingOnly
              assigned={self}
              label="Merge into"
              placeholder="Merge into…"
              onPick={(c) => c.kind === "tag" && setMerging(c.tag)}
            />
          </div>
        </div>
      </Card>

      <Card title="Delete">
        <p className={ui.muted}>
          {hasChildren
            ? "This tag has sub-tags; move or delete them first."
            : tag.count
              ? "This tag is in use; merge it into another tag instead."
              : "This tag is not used by any document."}
        </p>
        <button className={ui.secondary} disabled={hasChildren || tag.count > 0} onClick={() => setDeleting(true)}>
          <Trash2 size={15} aria-hidden /> Delete tag
        </button>
      </Card>

      {merging && (
        <Dialog
          title={`Merge “${tag.name}” into “${merging.path}”?`}
          confirmLabel="Merge"
          busy={merge.isPending}
          onClose={() => setMerging(null)}
          onConfirm={() => merge.mutate(merging.id, { onSuccess: onGone })}
        >
          <p>
            {tag.count} {tag.count === 1 ? "document moves" : "documents move"} to “{merging.path}” and “{tag.path}” is
            removed. This is recorded in the history.
          </p>
        </Dialog>
      )}
      {deleting && (
        <Dialog
          title={`Delete “${tag.path}”?`}
          confirmLabel="Delete"
          danger
          busy={remove.isPending}
          onClose={() => setDeleting(false)}
          onConfirm={() => remove.mutate(undefined, { onSuccess: onGone })}
        >
          <p>The tag is not used by any document. Documents are never deleted.</p>
        </Dialog>
      )}
    </div>
  );
}

function NewTagDialog({ onClose, onCreated }: { onClose: () => void; onCreated: (id: string) => void }) {
  const [name, setName] = useState("");
  const [parent, setParent] = useState<TagNode | null>(null);
  const none = useMemo(() => new Set<string>(), []);
  const create = useAction(
    () => api.post<{ message: string }>("/tags", { name: name.trim(), parent_id: parent?.id ?? null }),
    { invalidate: [["tags"]], success: () => "Tag created." },
  );
  return (
    <Dialog
      title="New tag"
      confirmLabel="Create"
      busy={create.isPending || !name.trim()}
      onClose={onClose}
      onConfirm={() => create.mutate(undefined, { onSuccess: (r) => onCreated(r.message) })}
    >
      <label className={ui.field}>
        Name
        <input className={ui.input} value={name} onChange={(e) => setName(e.target.value)} autoFocus />
      </label>
      <div className={ui.field}>
        Under
        {parent ? (
          <span className={ui.row}>
            <span className={ui.pill}>{parent.path}</span>
            <button type="button" className={ui.ghost} onClick={() => setParent(null)}>
              Change
            </button>
          </span>
        ) : (
          <TagPicker
            existingOnly
            assigned={none}
            label="Parent tag"
            placeholder="Top level (or type a parent tag)"
            onPick={(c) => c.kind === "tag" && setParent(c.tag)}
          />
        )}
      </div>
    </Dialog>
  );
}
