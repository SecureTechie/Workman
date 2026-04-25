export type Step =
  | "queued"
  | "detected"
  | "fetching"
  | "forking"
  | "cloning"
  | "setup"
  | "solving"
  | "pushing"
  | "done"
  | "skipped"
  | "failed"
  | "processing";

export interface Issue {
  id: string;
  title: string;
  step: Step;
  failed: boolean;
  pr_url: string | null;
  error: string | null;
  started_at: string;
  updated_at: string;
}

export interface QueueItem {
  id: string;
  repo: string;
  issue_number: number | null;
  title: string;
  url: string | null;
  difficulty: "EASY" | "MEDIUM" | "HARD" | "UNKNOWN";
  score: number;
  status: string;
  reason: string | null;
  failures: number;
  priority: boolean;
  is_current: boolean;
  rank: number;
}

export interface LogEntry {
  issue_id: string | null;
  message: string;
  ts: string;
}

export type LogRange = "1h" | "24h" | "3d";

export type WsMessage =
  | { type: "init"; issues: Issue[]; steps: Step[] }
  | { type: "issue_update"; issue: Issue }
  | { type: "log"; issue_id: string | null; message: string; ts: string };
