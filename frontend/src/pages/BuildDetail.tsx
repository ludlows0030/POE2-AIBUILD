import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api } from "../api/client";
import type { BuildCard } from "../types/build";
import { BuildCardView } from "../components/BuildCardView";

export function BuildDetail() {
  const { id } = useParams<{ id: string }>();
  const [build, setBuild] = useState<BuildCard | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  useEffect(() => {
    if (!id) return;
    api
      .getBuild(id)
      .then(setBuild)
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));
  }, [id]);

  if (loading) return <div className="loading">Loading build…</div>;
  if (error) return <div className="error-box">{error}</div>;
  if (!build) return <div className="error-box">Build not found</div>;

  return (
    <div>
      <Link to="/history" className="back-link">&larr; Back to history</Link>
      <BuildCardView build={build} />
    </div>
  );
}
