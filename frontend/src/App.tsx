import { useState } from "react";
import { useWorkman } from "./hooks/useWorkman";
import { IssueCard } from "./components/IssueCard";
import { LogConsole } from "./components/LogConsole";
import { QueuePanel } from "./components/QueuePanel";
import type { LogRange } from "./types";
import "./App.css";

export default function App() {
  const { issues, logs, steps, connected, range, setRange, control } = useWorkman();
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [feedback, setFeedback] = useState<{ msg: string; ok: boolean } | null>(null);
  const [paused, setPaused] = useState(false);
  const [rightTab, setRightTab] = useState<"logs" | "queue">("logs");

  const issueList = Object.values(issues).sort(
    (a, b) =>
      new Date(b.started_at).getTime() - new Date(a.started_at).getTime(),
  );

  const toggleSelect = (id: string) =>
    setSelectedId((prev) => (prev === id ? null : id));

  const filterLabel = selectedId
    ? `#${selectedId.split("#")[1] ?? selectedId}`
    : "all";

  async function handleControl(action: "skip-current" | "pause" | "resume") {
    try {
      await control(action);
      if (action === "pause") setPaused(true);
      if (action === "resume") setPaused(false);
      setFeedback({ msg: action === "skip-current" ? "Skipped" : action === "pause" ? "Paused" : "Resumed", ok: true });
    } catch {
      setFeedback({ msg: "Request failed", ok: false });
    } finally {
      setTimeout(() => setFeedback(null), 2500);
    }
  }

  return (
    <div className="layout">
      <header className="header">
        <img src="/workman.png" alt="Workman" className="logo" />
        <div className="header-right">
          <div className="ctrl-buttons">
            <button className="ctrl-btn" onClick={() => handleControl("skip-current")} title="Skip current task">
              Skip
            </button>
            {paused ? (
              <button className="ctrl-btn ctrl-btn--green" onClick={() => handleControl("resume")} title="Resume processing">
                Resume
              </button>
            ) : (
              <button className="ctrl-btn ctrl-btn--yellow" onClick={() => handleControl("pause")} title="Pause processing">
                Pause
              </button>
            )}
          </div>
          {feedback && (
            <span className={`ctrl-feedback ${feedback.ok ? "ctrl-feedback--ok" : "ctrl-feedback--err"}`}>
              {feedback.msg}
            </span>
          )}
          <span className="conn">
            <span className={`dot ${connected ? "on" : "off"}`} />
            {connected ? "connected" : "reconnecting..."}
          </span>
        </div>
      </header>

      <main className="main">
        <aside className="issues-panel">
          <div className="panel-title">Issues</div>
          {issueList.length === 0 ? (
            <div className="empty">
              No issues yet. Apply on Drips — Workman will take it from there.
            </div>
          ) : (
            issueList.map((issue) => (
              <IssueCard
                key={issue.id}
                issue={issue}
                steps={steps}
                selected={selectedId === issue.id}
                onClick={() => toggleSelect(issue.id)}
              />
            ))
          )}
        </aside>

        <section className="logs-panel">
          <div className="logs-header">
            <div className="tab-switcher">
              <button
                className={`tab-btn ${rightTab === "logs" ? "tab-btn--active" : ""}`}
                onClick={() => setRightTab("logs")}
              >Logs</button>
              <button
                className={`tab-btn ${rightTab === "queue" ? "tab-btn--active" : ""}`}
                onClick={() => setRightTab("queue")}
              >Queue</button>
            </div>
            {rightTab === "logs" && (
              <div className="logs-controls">
                <select
                  className="range-select"
                  value={range}
                  onChange={(e) => setRange(e.target.value as LogRange)}
                >
                  <option value="1h">Past 1 hour</option>
                  <option value="24h">Past 24 hours</option>
                  <option value="3d">Past 3 days</option>
                </select>
                <span className="filter-label">{filterLabel}</span>
              </div>
            )}
          </div>
          {rightTab === "logs"
            ? <LogConsole logs={logs} filterId={selectedId} />
            : <QueuePanel />}
        </section>
      </main>
    </div>
  );
}
