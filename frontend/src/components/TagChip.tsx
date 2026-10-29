import { Building2, Tag as TagIcon, X } from "lucide-react";
import type { TagRef } from "../api/client";
import styles from "./TagChip.module.css";

interface Props {
  tag: TagRef;
  /** show the whole path ("sender / Anna Nowak") instead of the last part */
  full?: boolean;
  onRemove?: () => void;
}

/** A tag as a small rounded chip; entity tags get a building icon. */
export function TagChip({ tag, full = false, onRemove }: Props) {
  const Icon = tag.entity_id ? Building2 : TagIcon;
  const parts = tag.path.split(" / ");
  return (
    <span className={styles.chip} title={tag.path}>
      <Icon size={11} strokeWidth={2.2} aria-hidden />
      {/* a long name is cut short ("…"), so the remove button always stays visible */}
      <span className={styles.label}>
        {full && parts.length > 1 && <span className={styles.parent}>{parts.slice(0, -1).join(" / ")} / </span>}
        {tag.name}
      </span>
      {onRemove && (
        <button className={styles.remove} onClick={onRemove} aria-label={`Remove tag ${tag.path}`}>
          <X size={12} />
        </button>
      )}
    </span>
  );
}
