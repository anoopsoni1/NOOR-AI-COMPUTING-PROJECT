import { lazy } from "react";
import { Navigate, Route, Routes } from "react-router-dom";
import Layout from "./components/Layout";

// Secondary pages load on demand; the Voice screen is part of the shell and always mounted.
const DashboardPage = lazy(() => import("./pages/DashboardPage"));
const CurrentTaskPage = lazy(() => import("./pages/CurrentTaskPage"));
const HistoryPage = lazy(() => import("./pages/HistoryPage"));
const PermissionsPage = lazy(() => import("./pages/PermissionsPage"));
const MemoriesPage = lazy(() => import("./pages/MemoriesPage"));
const SettingsPage = lazy(() => import("./pages/SettingsPage"));

export default function App() {
  return (
    <Routes>
      <Route element={<Layout />}>
        <Route index element={null} />
        <Route path="dashboard" element={<DashboardPage />} />
        <Route path="task" element={<CurrentTaskPage />} />
        <Route path="history" element={<HistoryPage />} />
        <Route path="history/:id" element={<HistoryPage />} />
        <Route path="permissions" element={<PermissionsPage />} />
        <Route path="memories" element={<MemoriesPage />} />
        <Route path="settings" element={<SettingsPage />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Route>
    </Routes>
  );
}
