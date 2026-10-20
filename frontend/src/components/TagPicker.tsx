import { Building2, Plus, Tag as TagIcon } from "lucide-react";
import { forwardRef, useId, useMemo, useState, type KeyboardEvent } from "react";
import type { EntityOut, TagNode } from "../api/client";
import { useEntities, useTags } from "../api/hooks";
import { choiceKey, choiceLabel, suggest, type TagChoice } from "../lib/tagChoices";
import styles from "./TagPicker.module.css";

const NO_TAGS: TagNode[] = [];
const NO_ENTITIES: EntityOut[] = [];

interface Props {
  /** tag ids to leave out of the suggestions */
  assigned: Set<string>;
  onPick: (choice: TagChoice) => void;
  placeholder?: string;
  /** open the suggestions upwards (for boxes at the bottom of the screen) */
  above?: boolean;
  disabled?: boolean;
  /** only offer existing tags (no new tags or entities) */
  existingOnly?: boolean;
  label?: string;
}

/** Type to find a tag, an entity or to create a tag; ↑/↓ and Enter pick, Escape leaves. */
export const TagPicker = forwardRef<HTMLInputElement, Props>(function TagPicker(
  { assigned, onPick, placeholder = "Add a tag…", above = false, disabled = false, existingOnly = false, label = "Add a tag" },
  ref,
) {
  const tags = useTags().data ?? NO_TAGS;
  const entities = useEntities().data ?? NO_ENTITIES;
  const [query, setQuery] = useState("");
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(0);
  const listId = useId();

  const choices = useMemo(() => {
    const all = suggest(query, tags, entities, assigned);
    return existingOnly ? all.filter((c) => c.kind === "tag") : all;
  }, [query, tags, entities, assigned, existingOnly]);
  const current = Math.min(active, choices.length - 1);

  const pick = (choice: TagChoice) => {
    onPick(choice);
    setQuery("");
    setActive(0);
  };

  const onKeyDown = (event: KeyboardEvent<HTMLInputElement>) => {
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      setOpen(true);
      const step = event.key === "ArrowDown" ? 1 : -1;
      setActive((current + step + choices.length) % Math.max(choices.length, 1));
    } else if (event.key === "Enter" && open && choices[current]) {
      event.preventDefault();
      pick(choices[current]);
    } else if (event.key === "Escape") {
      if (query) setQuery("");
      else event.currentTarget.blur();
      event.stopPropagation();
    }
  };

  return (
    <div className={styles.picker}>
      <input
        ref={ref}
        className={styles.input}
        value={query}
        placeholder={placeholder}
        disabled={disabled}
        role="combobox"
        aria-label={label}
        aria-expanded={open && choices.length > 0}
        aria-controls={listId}
        aria-activedescendant={open && choices[current] ? `${listId}-${current}` : undefined}
        autoComplete="off"
        onChange={(e) => {
          setQuery(e.target.value);
          setActive(0);
          setOpen(true);
        }}
        onFocus={() => setOpen(true)}
        onBlur={() => setOpen(false)}
        onKeyDown={onKeyDown}
      />
      {open && choices.length > 0 && (
        <ul id={listId} role="listbox" className={above ? `${styles.list} ${styles.above}` : styles.list}>
          {!query.trim() && <li className={styles.heading}>Most used</li>}
          {choices.map((choice, i) => (
            <li
              key={choiceKey(choice)}
              id={`${listId}-${i}`}
              role="option"
              aria-selected={i === current}
              className={i === current ? `${styles.option} ${styles.active}` : styles.option}
              onMouseDown={(e) => e.preventDefault()} // keep focus in the box
              onMouseEnter={() => setActive(i)}
              onClick={() => pick(choice)}
              title={choiceLabel(choice)}
            >
              <ChoiceView choice={choice} />
            </li>
          ))}
        </ul>
      )}
    </div>
  );
});

function ChoiceView({ choice }: { choice: TagChoice }) {
  if (choice.kind === "tag") {
    const parts = choice.tag.path.split(" / ");
    const Icon = choice.tag.entity_id ? Building2 : TagIcon;
    return (
      <>
        <Icon size={14} aria-hidden />
        <span className={styles.label}>
          {parts.length > 1 && <span className={styles.parent}>{parts.slice(0, -1).join(" / ")} / </span>}
          {choice.tag.name}
        </span>
        {choice.tag.total > 0 && <span className={styles.count}>{choice.tag.total}</span>}
      </>
    );
  }
  if (choice.kind === "entity") {
    return (
      <>
        <Building2 size={14} aria-hidden />
        <span className={styles.label}>
          <span className={styles.parent}>{choice.parent.path} / </span>
          {choice.entity.name}
          <span className={styles.sub}>
            {choice.entity.type_name}
            {choice.entity.aliases.length > 0 && ` · also ${choice.entity.aliases.join(", ")}`}
          </span>
        </span>
      </>
    );
  }
  return (
    <>
      <Plus size={14} aria-hidden />
      <span className={styles.label}>
        Create <strong>{choice.name}</strong>
        {choice.parent && <span className={styles.parent}> in {choice.parent.path}</span>}
      </span>
    </>
  );
}
