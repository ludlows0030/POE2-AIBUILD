import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api/client";
import type { BuildListItem } from "../types/build";

export function History() {
  const [builds, setBuilds] = useState<BuildListItem[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  useEffect(() => {
    api
      .listBuilds(50)
      .then((data) => {
        setBuilds(data.builds);
        setTotal(data.total);
      })
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));
  }, []);

  if (loading) return <div className="loading">Loading history…</div>;
  if (error) return <div className="error-box">{error}</div>;

  return (
    <div className="history-page">
      <h1>Build History</h1>
      <p className="subtitle">{total} builds generated</p>

      {builds.length === 0 ? (
        <div className="empty-state">
          <p>No builds yet.</p>
          <Link to="/" className="btn-generate">Create your first build</Link>
        </div>
      ) : (
        <div className="history-list">
          {builds.map((b) => (
            <Link to={`/builds/${b.id}`} key={b.id} className="history-row">
              <div className="hist-info">
                <strong>{b.build_name}</strong>
                <span className="muted">{b.core_skill}</span>
              </div>
              <div className="hist-meta">
                <span className={`badge ${b.confidence >= 0.7 ? "green" : "yellow"}`}>
                  {Math.round(b.confidence * 100)}%
                </span>
                <span className="muted">
                  {b.created_at ? new Date(b.created_at).toLocaleDateString() : ""}
                </span>
              </div>
            </Link>
          ))}
        </div>
      )}
    </div>
  );
}
