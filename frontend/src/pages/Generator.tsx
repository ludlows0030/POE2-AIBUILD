import { useState } from "react";
import { api } from "../api/client";
import type { BuildCard } from "../types/build";
import { BuildCardView } from "../components/BuildCardView";

const EXAMPLES = [
  "电系法师 Spark，中等预算，能刷图能打王",
  "Lightning Arrow Deadeye for fast mapping, low budget",
  "冰暴武僧 Ice Strike，CI, ES 流，高预算打王",
  "Minion Witch SRS, safe all-content build, medium budget",
];

export function Generator() {
  const [request, setRequest] = useState("");
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState<BuildCard | null>(null);
  const [error, setError] = useState("");

  const handleGenerate = async () => {
    if (!request.trim() || request.trim().length < 5) return;
    setLoading(true);
    setError("");
    setResult(null);
    try {
      const data = await api.generate({ user_request: request.trim() });
      setResult(data);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Unknown error");
    } finally {
      setLoading(false);
    }
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleGenerate();
    }
  };

  return (
    <div className="generator">
      <section className="hero">
        <h1>POE2 Build Architect</h1>
        <p className="subtitle">
          Describe your ideal build in natural language — AI will design it.
        </p>

        <div className="input-area">
          <textarea
            value={request}
            onChange={(e) => setRequest(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder="Example: 我想玩一个电系法师，中等预算，速刷地图…"
            rows={3}
            disabled={loading}
          />
          <button
            onClick={handleGenerate}
            disabled={loading || request.trim().length < 5}
            className="btn-generate"
          >
            {loading ? <span className="spinner" /> : null}
            {loading ? "Generating…" : "Generate Build"}
          </button>
        </div>

        <div className="examples">
          {EXAMPLES.map((ex) => (
            <button
              key={ex}
              className="example-chip"
              onClick={() => setRequest(ex)}
            >
              {ex.length > 40 ? ex.slice(0, 40) + "…" : ex}
            </button>
          ))}
        </div>
      </section>

      {error && <div className="error-box">{error}</div>}

      {result && <BuildCardView build={result} />}
    </div>
  );
}
