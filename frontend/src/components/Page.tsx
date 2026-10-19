import type { ReactNode } from "react";
import styles from "./Page.module.css";

interface Props {
  title: string;
  subtitle?: ReactNode;
  actions?: ReactNode;
  children: ReactNode;
  /** content fills the page (e.g. list + detail) instead of a centered column */
  wide?: boolean;
}

/** Frame of a management page: title row, then scrolling content. */
export function Page({ title, subtitle, actions, children, wide = false }: Props) {
  return (
    <main className={styles.page}>
      <header className={styles.header}>
        <div>
          <h1>{title}</h1>
          {subtitle && <p className={styles.subtitle}>{subtitle}</p>}
        </div>
        {actions && <div className={styles.actions}>{actions}</div>}
      </header>
      <div className={wide ? styles.wide : styles.body}>{children}</div>
    </main>
  );
}

/** A titled block inside a page. */
export function Card({ title, actions, children }: { title?: ReactNode; actions?: ReactNode; children: ReactNode }) {
  return (
    <section className={styles.card}>
      {(title || actions) && (
        <div className={styles.cardHeader}>
          {title && <h2>{title}</h2>}
          {actions}
        </div>
      )}
      {children}
    </section>
  );
}

export function Empty({ children }: { children: ReactNode }) {
  return <p className={styles.empty}>{children}</p>;
}
