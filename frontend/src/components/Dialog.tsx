import { useEffect, useRef, type FormEvent, type ReactNode } from "react";
import ui from "../styles/ui.module.css";
import styles from "./Dialog.module.css";

interface Props {
  title: string;
  children?: ReactNode;
  confirmLabel: string;
  danger?: boolean;
  busy?: boolean;
  onConfirm: () => void;
  onClose: () => void;
}

/** Modal question with Cancel / confirm buttons; Escape cancels. Fields inside get the first focus. */
export function Dialog({ title, children, confirmLabel, danger = false, busy = false, onConfirm, onClose }: Props) {
  const dialog = useRef<HTMLDialogElement>(null);
  const confirm = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    dialog.current?.showModal();
    // Enter confirms ordinary questions; destructive ones keep the focus on Cancel, forms on their first field
    if (!danger && !dialog.current?.querySelector("input, textarea")) confirm.current?.focus();
  }, [danger]);
  const submit = (event: FormEvent) => {
    event.preventDefault();
    onConfirm();
  };
  return (
    <dialog ref={dialog} className={styles.dialog} onClose={onClose}>
      <form onSubmit={submit}>
        <h3>{title}</h3>
        <div className={styles.body}>{children}</div>
        <div className={styles.buttons}>
          <button type="button" className={ui.secondary} onClick={() => dialog.current?.close()}>
            Cancel
          </button>
          <button ref={confirm} type="submit" className={danger ? ui.danger : ui.button} disabled={busy}>
            {confirmLabel}
          </button>
        </div>
      </form>
    </dialog>
  );
}
