import { useCallback, useEffect, useRef, useState } from "react";
import type { QueueItem } from "../types";
import { fetchQueue, retryTask } from "../hooks/useWorkman";
import styles from "./QueuePanel.module.css";

const DIFF_COLOR: Record<string, string> = {
  EASY: "var(--green)",
  MEDIUM: "var(--yellow)",
  HARD: "var(--red)",
  UNKNOWN: "var(--muted)",
};

const STATUS_COLOR: Record<string, string> = {
  processing: "var(--yellow)",
  queued:     "var(--muted)",
  done:       "var(--green)",
  skipped:    "var(--red)",
  failed:     "var(--red)",
  deferred:   "var(--muted)",
};

function elapsed(iso: string | null): string {
  if (!iso) return "";
  const secs = Math.floor((Date.now() - new Date(iso).getTime()) / 1000);
  if (secs < 60) return `${secs}s`;
  if (secs < 3600) return `${Math.floor(secs / 60)}m ${secs % 60}s`;
  return `${Math.floor(secs / 3600)}h ${Math.floor((secs % 3600) / 60)}m`;
}

// Format id as "owner/repo#number" — never "#number#number"
function fmtId(item: QueueItem): string {
  if (item.issue_number != null) return `${item.repo}#${item.issue_number}`;
  return item.id;
}

export function QueuePanel() {
  const [queue, setQueue] = useState<QueueItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [feedback, setFeedback] = useState<Record<string, string>>({});
  const [tick, setTick] = useState(0);
  const timerRef = useRef<ReturnType<typeof setTimeout>>(undefined);
  const tickRef  = useRef<ReturnType<typeof setInterval>>(undefined);

  const refresh = useCallback(async () => {
    setLoading(true);
    try {
      setQueue(await fetchQueue());
    } catch {
      // keep stale data
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    refresh();
    // Re-tick every second so elapsed time updates live
    tickRef.current = setInterval(() => setTick((t) => t + 1), 1000);
    return () => clearInterval(tickRef.current);
  }, [refresh]);

  // Suppress unused-variable warning — tick is only used to force re-render
  void tick;

  async function handleRetry(item: QueueItem) {
    if (!item.issue_number) return;
    try {
      await retryTask(item.repo, item.issue_number);
      setFeedback((f) => ({ ...f, [item.id]: "Queued ✓" }));
      clearTimeout(timerRef.current);
      timerRef.current = setTimeout(() => {
        setFeedback((f) => { const n = { ...f }; delete n[item.id]; return n; });
      }, 2500);
      await refresh();
    } catch {
      setFeedback((f) => ({ ...f, [item.id]: "Failed" }));
    }
  }

  return (
    <div className={styles.panel}>
      <div className={styles.header}>
        <span>Execution Queue</span>
        <button className={styles.refreshBtn} onClick={refresh} disabled={loading}>
          {loading ? "..." : "Refresh"}
        </button>
      </div>

      {queue.length === 0 ? (
        <div className={styles.empty}>
          {loading ? "Loading..." : "No assigned issues found."}
        </div>
      ) : (
        <div className={styles.list}>
          {queue.map((item) => {
            const isSkipped   = item.status === "skipped";
            const isCurrent   = item.is_current;
            const isStalled   = item.stalled;

            return (
              <div
                key={item.id}
                className={[
                  styles.row,
                  isCurrent  ? styles.current  : "",
                  isSkipped  ? styles.skipped  : "",
                  isStalled  ? styles.stalled  : "",
                ].join(" ")}
              >
                {/* ── top row: rank / badges / difficulty / status ── */}
                <div className={styles.rowTop}>
                  <span className={styles.rank}>#{item.rank}</span>
                  {item.priority  && <span className={styles.badge}>PRIORITY</span>}
                  {isCurrent      && <span className={styles.badgeCurrent}>ACTIVE</span>}
                  {isStalled      && <span className={styles.badgeStalled}>STALLED</span>}
                  <span className={styles.diff} style={{ color: DIFF_COLOR[item.difficulty] }}>
                    {item.difficulty}
                  </span>
                  <span className={styles.status} style={{ color: STATUS_COLOR[item.status] ?? "var(--muted)" }}>
                    {item.status.toUpperCase()}
                  </span>
                </div>

                {/* ── title / link — always "owner/repo#number" ── */}
                <div className={styles.title}>
                  {item.url ? (
                    <a href={item.url} target="_blank" rel="noreferrer" className={styles.link}>
                      {fmtId(item)} — {item.title}
                    </a>
                  ) : (
                    <span>{fmtId(item)} — {item.title}</span>
                  )}
                </div>

                {/* ── progress bar for active issue ── */}
                {isCurrent && (
                  <div className={styles.progressWrap}>
                    <div className={styles.progressMeta}>
                      <span className={styles.stepLabel}>{item.current_step}</span>
                      <span className={styles.progressPct}>{item.progress_percent}%</span>
                      {item.started_at && (
                        <span className={styles.elapsed}>⏱ {elapsed(item.started_at)}</span>
                      )}
                    </div>
                    <div className={styles.progressBar}>
                      <div
                        className={styles.progressFill}
                        style={{ width: `${item.progress_percent}%` }}
                      />
                    </div>
                  </div>
                )}

                {/* ── stall warning ── */}
                {isStalled && (
                  <div className={styles.stallWarning}>
                    ⚠ Stalled — manual review recommended
                  </div>
                )}

                {/* ── skip reason ── */}
                {isSkipped && (
                  <div className={styles.reason}>
                    {item.reason ?? "Complex task — revisit later"}
                  </div>
                )}

                {/* ── failure count ── */}
                {item.failures > 0 && (
                  <div className={styles.failures}>
                    {item.failures} failed attempt{item.failures !== 1 ? "s" : ""}
                  </div>
                )}

                {/* ── retry button on skipped tasks ── */}
                {isSkipped && item.issue_number && (
                  <div className={styles.actions}>
                    {feedback[item.id] ? (
                      <span className={styles.fb}>{feedback[item.id]}</span>
                    ) : (
                      <button className={styles.retryBtn} onClick={() => handleRetry(item)}>
                        Retry / Prioritize
                      </button>
                    )}
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
