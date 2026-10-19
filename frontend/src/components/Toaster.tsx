import { CircleAlert, X } from "lucide-react";
import styles from "./Toaster.module.css";
import { dismiss, useToasts } from "./toast";

export function Toaster() {
  const toasts = useToasts();
  return (
    <div className={styles.region} role="status" aria-live="polite">
      {toasts.map((t) => (
        <div key={t.id} className={t.kind === "error" ? `${styles.toast} ${styles.error}` : styles.toast}>
          {t.kind === "error" && <CircleAlert size={16} aria-hidden />}
          <span>{t.message}</span>
          {t.action && (
            <button
              className={styles.action}
              onClick={() => {
                dismiss(t.id);
                t.action!.run();
              }}
            >
              {t.action.label}
            </button>
          )}
          <button onClick={() => dismiss(t.id)} aria-label="Dismiss">
            <X size={14} />
          </button>
        </div>
      ))}
    </div>
  );
}
