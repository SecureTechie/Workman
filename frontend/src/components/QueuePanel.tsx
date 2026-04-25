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

export function QueuePanel() {
  const [queue, setQueue] = useState<QueueItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [feedback, setFeedback] = useState<Record<string, string>>({});
  const timerRef = useRef<ReturnType<typeof setTimeout>>(undefined);

  const refresh = useCallback(async () => {
    setLoading(true);
    try {
      setQueue(await fetchQueue());
    } catch {
      // silently keep stale data
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh]);

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
        <div className={styles.empty}>Queue is empty.</div>
      ) : (
        <div className={styles.list}>
          {queue.map((item) => {
            const isSkipped = item.status === "skipped";
            const isCurrent = item.is_current;
            return (
              <div
                key={item.id}
                className={`${styles.row} ${isCurrent ? styles.current : ""} ${isSkipped ? styles.skipped : ""}`}
              >
                <div className={styles.rowTop}>
                  <span className={styles.rank}>#{item.rank}</span>
                  {item.priority && <span className={styles.badge}>PRIORITY</span>}
                  {isCurrent && <span className={styles.badgeCurrent}>ACTIVE</span>}
                  <span
                    className={styles.diff}
                    style={{ color: DIFF_COLOR[item.difficulty] }}
                  >
                    {item.difficulty}
                  </span>
                  <span className={styles.status}>{item.status.toUpperCase()}</span>
                </div>

                <div className={styles.title}>
                  {item.url ? (
                    <a href={item.url} target="_blank" rel="noreferrer" className={styles.link}>
                      {item.repo}#{item.issue_number} — {item.title}
                    </a>
                  ) : (
                    <span>{item.title}</span>
                  )}
                </div>

                {isSkipped && (
                  <div className={styles.reason}>
                    {item.reason ?? "Complex task — revisit later"}
                  </div>
                )}

                {item.failures > 0 && (
                  <div className={styles.failures}>
                    {item.failures} failed attempt{item.failures !== 1 ? "s" : ""}
                  </div>
                )}

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
