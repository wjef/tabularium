import { Building2, Filter, Tag as TagIcon, X } from "lucide-react";
import { useMemo, useState } from "react";
import { useEntities, useTags, type DocumentFilters } from "../api/hooks";
import { TagPicker } from "../components/TagPicker";
import styles from "./FilterBar.module.css";

interface Props {
  filters: DocumentFilters;
  update: (changes: Record<string, string | string[] | null>) => void;
}

/**
 * The tags and entities the list is narrowed to, as removable chips, plus "+ Filter" to add
 * another tag. Tags from different branches must all match (FV and Recipient / Jan);
 * tags in the same branch mean any of them (FV / correction or FV / pro forma).
 */
export function FilterBar({ filters, update }: Props) {
  const tags = useTags().data ?? [];
  const entities = useEntities().data ?? [];
  const [adding, setAdding] = useState(false);
  const tagIds = useMemo(() => filters.tag ?? [], [filters.tag]);
  const entityIds = filters.entity ?? [];
  const selected = useMemo(() => new Set(tagIds), [tagIds]);

  const setTags = (ids: string[]) => update({ tag: ids, doc: null });
  const chips = tagIds.map((id) => tags.find((t) => t.id === id)).filter((t) => t !== undefined);

  return (
    <div className={styles.bar}>
      {chips.map((t) => (
        <span key={t.id} className={styles.chip} title={t.path}>
          {t.entity_id ? <Building2 size={12} aria-hidden /> : <TagIcon size={12} aria-hidden />}
          {t.path}
          <button onClick={() => setTags(tagIds.filter((x) => x !== t.id))} aria-label={`Remove filter ${t.path}`}>
            <X size={12} />
          </button>
        </span>
      ))}
      {entityIds.map((id) => {
        const e = entities.find((x) => x.id === id);
        return (
          <span key={id} className={styles.chip}>
            <Building2 size={12} aria-hidden />
            {e?.name ?? "Entity"}
            <button
              onClick={() => update({ entity: entityIds.filter((x) => x !== id), doc: null })}
              aria-label={`Remove filter ${e?.name ?? "entity"}`}
            >
              <X size={12} />
            </button>
          </span>
        );
      })}
      {adding ? (
        <div className={styles.picker}>
          <TagPicker
            existingOnly
            assigned={selected}
            label="Filter by tag"
            placeholder="Filter by tag…"
            onPick={(choice) => {
              if (choice.kind === "tag") setTags([...tagIds, choice.tag.id]);
              setAdding(false);
            }}
          />
          <button className={styles.cancel} onClick={() => setAdding(false)} aria-label="Cancel">
            <X size={13} />
          </button>
        </div>
      ) : (
        <button
          className={styles.add}
          onClick={() => setAdding(true)}
          title="Narrow the list by another tag (Ctrl+click a tag in the sidebar does the same)"
        >
          <Filter size={12} aria-hidden /> Filter
        </button>
      )}
      {chips.length + entityIds.length > 1 && (
        <button className={styles.clear} onClick={() => update({ tag: null, entity: null, related: null, doc: null })}>
          Clear all
        </button>
      )}
    </div>
  );
}
