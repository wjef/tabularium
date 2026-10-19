import styles from "./App.module.css";
import { DocumentList } from "./DocumentList";
import { DocumentView } from "./DocumentView";
import { Sidebar } from "./Sidebar";

/** Three-pane workspace: sidebar · document list · document. */
export function App() {
  return (
    <div className={styles.shell}>
      <Sidebar />
      <DocumentList />
      <DocumentView />
    </div>
  );
}
