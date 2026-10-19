import { Navigate, Route, Routes } from "react-router";
import { Toaster } from "../components/Toaster";
import { EntitiesPage } from "../pages/EntitiesPage";
import { HistoryPage } from "../pages/HistoryPage";
import { MaintenancePage } from "../pages/MaintenancePage";
import { OutdatedPage } from "../pages/OutdatedPage";
import { TagsPage } from "../pages/TagsPage";
import styles from "./App.module.css";
import { DocumentList } from "./DocumentList";
import { DocumentView } from "./DocumentView";
import { Sidebar } from "./Sidebar";

/** Sidebar on the left; the workspace (list · document) or a management page next to it. */
export function App() {
  return (
    <>
      <Routes>
        <Route
          index
          element={
            <div className={styles.shell}>
              <Sidebar />
              <DocumentList />
              <DocumentView />
            </div>
          }
        />
        <Route path="outdated" element={<PageShell page={<OutdatedPage />} />} />
        <Route path="tags" element={<PageShell page={<TagsPage />} />} />
        <Route path="entities" element={<PageShell page={<EntitiesPage />} />} />
        <Route path="history" element={<PageShell page={<HistoryPage />} />} />
        <Route path="maintenance" element={<PageShell page={<MaintenancePage />} />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
      <Toaster />
    </>
  );
}

function PageShell({ page }: { page: React.ReactNode }) {
  return (
    <div className={`${styles.shell} ${styles.pageShell}`}>
      <Sidebar />
      {page}
    </div>
  );
}
