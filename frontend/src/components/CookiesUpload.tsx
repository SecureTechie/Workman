import { useEffect, useRef, useState } from "react";
import styles from "./CookiesUpload.module.css";

const API_URL = import.meta.env.VITE_API_URL ?? "http://localhost:8000";

type Status = "loading" | "missing" | "ok" | "uploading" | "error";

export function CookiesUpload() {
  const [status, setStatus] = useState<Status>("loading");
  const [msg, setMsg] = useState("");
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    fetch(`${API_URL}/api/cookies/status`)
      .then((r) => r.json())
      .then((d) => setStatus(d.exists ? "ok" : "missing"))
      .catch(() => setStatus("missing"));
  }, []);

  async function handleFile(file: File) {
    setStatus("uploading");
    setMsg("");
    try {
      const form = new FormData();
      form.append("file", file);
      const res = await fetch(`${API_URL}/api/cookies`, { method: "POST", body: form });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail ?? "Upload failed");
      setStatus("ok");
      setMsg(`${data.count} cookies loaded`);
    } catch (e: unknown) {
      setStatus("error");
      setMsg(e instanceof Error ? e.message : "Upload failed");
    }
  }

  function onInputChange(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    if (file) handleFile(file);
    e.target.value = "";
  }

  const label =
    status === "loading"   ? "cookies..."
    : status === "uploading" ? "uploading..."
    : status === "ok"        ? `✓ cookies${msg ? ` (${msg})` : ""}`
    : status === "error"     ? `✗ ${msg}`
    : "✗ no cookies";

  return (
    <button
      className={`${styles.btn} ${styles[status]}`}
      onClick={() => inputRef.current?.click()}
      title="Click to upload cookies.json from drips.network"
      disabled={status === "uploading" || status === "loading"}
    >
      {label}
      <input
        ref={inputRef}
        type="file"
        accept=".json,application/json"
        style={{ display: "none" }}
        onChange={onInputChange}
      />
    </button>
  );
}
