import { useEffect, useRef } from "react";
import styles from "./ShortcutsHelp.module.css";

const GROUPS: { title: string; keys: [string[], string][] }[] = [
  {
    title: "Anywhere",
    keys: [
      [["Ctrl", "K"], "Find anything / run a command"],
      [["?"], "This list"],
      [["G", "A"], "All documents"],
      [["G", "I"], "To be tagged"],
      [["G", "T"], "Manage tags"],
      [["G", "E"], "Entities"],
    ],
  },
  {
    title: "Document list",
    keys: [
      [["/"], "Search"],
      [["J"], "Next document"],
      [["K"], "Previous document"],
      [["Ctrl", "Click"], "Pick several documents"],
      [["Shift", "Click"], "Pick a range"],
      [["Esc"], "Clear the picked documents"],
    ],
  },
  {
    title: "Open document",
    keys: [
      [["T"], "Add a tag"],
      [["D"], "Done tagging / revise tagging"],
      [["E"], "Edit the title"],
      [["I"], "Show or hide details"],
      [["O"], "Open in the default app"],
    ],
  },
];

/** Overlay listing the keyboard shortcuts. */
export function ShortcutsHelp({ onClose }: { onClose: () => void }) {
  const dialog = useRef<HTMLDialogElement>(null);
  useEffect(() => dialog.current?.showModal(), []);
  return (
    <dialog
      ref={dialog}
      className={styles.help}
      onClose={onClose}
      onClick={(e) => e.target === dialog.current && dialog.current?.close()}
      aria-label="Keyboard shortcuts"
    >
      <header>
        <h2>Keyboard shortcuts</h2>
        <button onClick={() => dialog.current?.close()} autoFocus>
          Close
        </button>
      </header>
      <div className={styles.groups}>
        {GROUPS.map((g) => (
          <section key={g.title}>
            <h3>{g.title}</h3>
            <dl>
              {g.keys.map(([keys, label]) => (
                <div key={label}>
                  <dt>
                    {keys.map((k, i) => (
                      <kbd key={i}>{k}</kbd>
                    ))}
                  </dt>
                  <dd>{label}</dd>
                </div>
              ))}
            </dl>
          </section>
        ))}
      </div>
    </dialog>
  );
}
