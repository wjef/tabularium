import { useLayoutEffect, useRef, useState, type KeyboardEvent, type RefObject } from "react";
import styles from "./EditableText.module.css";

interface Props {
  value: string;
  /** shown (dimmed) when there is no value */
  placeholder: string;
  label: string;
  onSave: (value: string) => void;
  multiline?: boolean;
  className?: string;
  /** lets a keyboard shortcut start editing (by clicking the text) */
  buttonRef?: RefObject<HTMLButtonElement | null>;
}

/**
 * Text that turns into a field on click. Enter (Ctrl+Enter for several lines) or leaving the
 * field saves; Escape cancels.
 */
export function EditableText({ value, placeholder, label, onSave, multiline = false, className = "", buttonRef }: Props) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(value);
  const area = useRef<HTMLTextAreaElement>(null);

  useLayoutEffect(() => {
    const node = area.current;
    if (!node) return;
    node.style.height = "auto";
    node.style.height = `${node.scrollHeight + 2}px`; // grow with the text
  }, [draft, editing]);

  const start = () => {
    setDraft(value);
    setEditing(true);
  };
  const finish = (save: boolean) => {
    setEditing(false);
    if (save && draft.trim() !== value.trim()) onSave(draft);
  };
  const onKeyDown = (event: KeyboardEvent) => {
    if (event.key === "Escape") {
      event.preventDefault();
      finish(false);
    } else if (event.key === "Enter" && (!multiline || event.ctrlKey || event.metaKey)) {
      event.preventDefault();
      finish(true);
    }
  };

  if (editing) {
    const common = {
      className: `${className} ${styles.field}`,
      value: draft,
      autoFocus: true,
      "aria-label": label,
      placeholder,
      onChange: (e: { target: { value: string } }) => setDraft(e.target.value),
      onBlur: () => finish(true),
      onKeyDown,
      onFocus: (e: { target: HTMLInputElement | HTMLTextAreaElement }) => e.target.select(),
    };
    return multiline ? <textarea ref={area} rows={2} {...common} /> : <input {...common} />;
  }
  return (
    <button
      ref={buttonRef}
      type="button"
      className={`${className} ${styles.display} ${value ? "" : styles.placeholder}`}
      onClick={start}
      title={`${label} (click to edit)`}
      aria-label={value ? `${label}: ${value}. Click to edit.` : `${label}: ${placeholder}`}
    >
      {value || placeholder}
    </button>
  );
}
