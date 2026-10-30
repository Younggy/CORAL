import { useEffect, useState } from "react";
import { api, type RunStatus } from "../lib/api";
import { useSSE } from "../hooks/useSSE";
import RunSelector from "./RunSelector";
import SettingsMenu from "./SettingsMenu";

type Tab = "overview" | "dag" | "knowledge" | "logs";

interface Props {
  activeTab: Tab;
  onTabChange: (tab: Tab) => void;
}

const tabs: { key: Tab; label: string; path: string }[] = [
  { key: "overview", label: "Overview", path: "M3 3h7v7H3z M14 3h7v7h-7z M3 14h7v7H3z M14 14h7v7h-7z" },
  { key: "dag", label: "Lineage", path: "M8 5h8 M5 8v8 M8 19h8 M19 8v8 M2 2h6v6H2z M16 2h6v6h-6z M2 16h6v6H2z M16 16h6v6h-6z" },
  { key: "knowledge", label: "Knowledge", path: "M12 5v16 M12 5C9 3 5 3 2 4v15c3-1 7-1 10 2 3-3 7-3 10-2V4c-3-1-7-1-10 1Z" },
  { key: "logs", label: "Logs", path: "m4 7 5 5-5 5 M12 17h8" },
];

export default function Header({ activeTab, onTabChange }: Props) {
  const [status, setStatus] = useState<RunStatus | null>(null);

  const refresh = () => {
    api.status().then(setStatus).catch(() => {});
  };

  useEffect(refresh, []);
  useSSE({
    "attempt:new": refresh,
    "attempt:update": refresh,
    "eval:update": refresh,
  });

  const activeAgents = status?.agents.filter((a) => a.status === "active").length ?? 0;

  return (
    <>
      <a className="skip-link" href="#main-content">Skip to content</a>
      <aside className="sidebar">
        <div className="brand">
          <svg width="28" height="32" viewBox="0 0 28 32" fill="none" aria-hidden="true">
            <path d="M14 28V5M14 19 5 12V5M14 13l9-7M14 25l9-7v-5" stroke="currentColor" strokeWidth="3" strokeLinecap="round" strokeLinejoin="round" />
          </svg>
          <span>CORAL</span>
        </div>
        <nav className="primary-nav" aria-label="Main navigation">
          {tabs.map((t) => (
            <button
              key={t.key}
              onClick={() => onTabChange(t.key)}
              aria-current={activeTab === t.key ? "page" : undefined}
              className={`nav-item ${activeTab === t.key ? "is-active" : ""}`}
            >
              <svg width="19" height="19" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d={t.path} /></svg>
              <span>{t.label}</span>
            </button>
          ))}
        </nav>
        <div className="sidebar-footer">Research workspace</div>
      </aside>
      <header className="topbar">
        <div className="run-context"><RunSelector /></div>
        <div className="run-stats" aria-label="Run status">
          {status && (
            <>
              <span className="run-health">
                <span
                  className={`w-1.5 h-1.5 rounded-full ${
                    status.manager_alive ? "bg-green-500" : "bg-border-strong"
                  }`}
                />
                {activeAgents > 0
                  ? `${activeAgents} active`
                  : status.manager_alive
                  ? "idle"
                  : "stopped"}
              </span>
              <span className="run-stat"><span>Attempts</span><strong>{status.total_attempts}</strong></span>
              {status.best_score != null && (
                <span className="run-stat"><span>Best</span><strong>{status.best_score.toFixed(4)}</strong></span>
              )}
              <span className="run-stat"><span>Evaluations</span><strong>{status.eval_count}</strong></span>

    </>
        )}
      </div>

      <SettingsMenu />
    </header>
    </>
  );
}
