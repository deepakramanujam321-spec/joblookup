import { useEffect, useState } from "react";
import { NavLink, Navigate, Route, Routes } from "react-router-dom";
import { useOverview } from "./api/hooks";
import { DashboardPage } from "./pages/Dashboard";
import { JobsPage } from "./pages/Jobs";
import { ApplicationsPage } from "./pages/Applications";
import { ProfilePage } from "./pages/Profile";
import { InsightsPage } from "./pages/Insights";
import { SettingsPage } from "./pages/Settings";
import { PipelinePage } from "./pages/Pipeline";

const NAV = [
  { to: "/", label: "Overview", icon: "◧", end: true },
  { to: "/jobs", label: "Jobs", icon: "☰", count: "worth_reviewing" as const },
  { to: "/applications", label: "Applications", icon: "▦", count: "in_progress" as const },
  { to: "/profile", label: "Profile", icon: "◉" },
  { to: "/insights", label: "Insights", icon: "✦" },
  { to: "/pipeline", label: "Pipeline", icon: "⟳" },
  { to: "/settings", label: "Settings", icon: "⚙" },
];

type Theme = "system" | "light" | "dark";

function useTheme(): [Theme, (t: Theme) => void] {
  const [theme, setTheme] = useState<Theme>(() => {
    try {
      return (localStorage.getItem("joblookup.theme") as Theme) || "system";
    } catch {
      return "system";
    }
  });
  useEffect(() => {
    if (theme === "system") document.documentElement.removeAttribute("data-theme");
    else document.documentElement.setAttribute("data-theme", theme);
    try {
      localStorage.setItem("joblookup.theme", theme);
    } catch {
      /* storage unavailable */
    }
  }, [theme]);
  return [theme, setTheme];
}

export function App() {
  const overview = useOverview();
  const [theme, setTheme] = useTheme();
  return (
    <div className="shell">
      <nav className="sidebar" aria-label="Main">
        <div className="brand"><span className="brand-mark" aria-hidden="true">JL</span>JobLookup</div>
        {NAV.map((item) => (
          <NavLink key={item.to} to={item.to} end={item.end} className={({ isActive }) => `nav-link${isActive ? " active" : ""}`}>
            <span aria-hidden="true">{item.icon}</span>
            {item.label}
            {item.count && overview.data && overview.data[item.count] > 0 && (
              <span className="nav-count" aria-label={`${overview.data[item.count]} items`}>{overview.data[item.count]}</span>
            )}
          </NavLink>
        ))}
        <div className="sidebar-foot">
          <label className="small muted" htmlFor="theme">Theme</label>
          <select id="theme" className="select" value={theme} onChange={(e) => setTheme(e.target.value as Theme)}>
            <option value="system">System</option>
            <option value="light">Light</option>
            <option value="dark">Dark</option>
          </select>
        </div>
      </nav>
      <main className="main">
        <Routes>
          <Route path="/" element={<DashboardPage />} />
          <Route path="/jobs" element={<JobsPage />} />
          <Route path="/jobs/:jobId" element={<JobsPage />} />
          <Route path="/applications" element={<ApplicationsPage />} />
          <Route path="/profile" element={<ProfilePage />} />
          <Route path="/insights" element={<InsightsPage />} />
          <Route path="/pipeline" element={<PipelinePage />} />
          <Route path="/settings" element={<SettingsPage />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </main>
      <nav className="mobile-nav" aria-label="Main">
        {NAV.slice(0, 5).map((item) => (
          <NavLink key={item.to} to={item.to} end={item.end} className={({ isActive }) => (isActive ? "active" : "")}>
            <span aria-hidden="true" style={{ fontSize: 16 }}>{item.icon}</span>
            {item.label}
          </NavLink>
        ))}
      </nav>
    </div>
  );
}
