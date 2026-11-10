import { useCallback, useState } from "react";

const API = "";

export default function App() {
  const [query, setQuery] = useState({
    short_description: "",
    description: "",
    category: "",
    assignment_group: "",
  });
  const [result, setResult] = useState(null);
  const [history, setHistory] = useState([]);
  const [selectedIdx, setSelectedIdx] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [indexInfo, setIndexInfo] = useState(null);

  const handleSubmit = useCallback(
    async (e) => {
      e.preventDefault();
      if (!query.short_description.trim()) return;
      setLoading(true);
      setError(null);
      try {
        const res = await fetch(`${API}/api/query`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(query),
        });
        if (!res.ok) {
          const detail = await res.json().catch(() => ({}));
          throw new Error(detail.detail || `Request failed (${res.status})`);
        }
        const data = await res.json();
        setResult(data);
        setHistory((prev) => [
          { query: { ...query }, result: data, time: new Date() },
          ...prev,
        ]);
        setSelectedIdx(0);
      } catch (err) {
        setError(err.message);
      } finally {
        setLoading(false);
      }
    },
    [query]
  );

  function selectHistory(idx) {
    const entry = history[idx];
    if (!entry) return;
    setQuery(entry.query);
    setResult(entry.result);
    setSelectedIdx(idx);
    setError(null);
  }

  async function checkIndex() {
    try {
      const res = await fetch(`${API}/api/index/status`);
      setIndexInfo(await res.json());
    } catch (err) {
      setIndexInfo({ error: err.message });
    }
  }

  return (
    <div className="window">
      {/* Title bar */}
      <div className="titlebar">
        <div className="traffic-lights">
          <span className="dot dot-red" />
          <span className="dot dot-yellow" />
          <span className="dot dot-green" />
        </div>
        <span className="titlebar-text">Bucky</span>
      </div>

      {/* Body */}
      <div className="body">
        {/* Sidebar — query history */}
        <aside className="sidebar">
          <div className="sidebar-header">History</div>
          <div className="sidebar-list">
            {history.length === 0 ? (
              <div className="sidebar-empty">
                Queries you run will appear here.
              </div>
            ) : (
              history.map((entry, i) => (
                <button
                  key={`${i}-${entry.time.getTime()}`}
                  type="button"
                  className="sidebar-item"
                  aria-selected={selectedIdx === i}
                  onClick={() => selectHistory(i)}
                >
                  <span className="sidebar-item-title">
                    {entry.query.short_description}
                  </span>
                  <span className="sidebar-item-meta">
                    {entry.result.retrieval_count} matches ·{" "}
                    {entry.result.recommendation.confidence}
                  </span>
                </button>
              ))
            )}
          </div>
        </aside>

        {/* Main */}
        <div className="main">
          {/* Query form */}
          <form className="query-form" onSubmit={handleSubmit}>
            <h2>Resolve a ticket</h2>
            <div className="field">
              <label htmlFor="sd">Short description</label>
              <input
                id="sd"
                type="text"
                value={query.short_description}
                onChange={(e) =>
                  setQuery({ ...query, short_description: e.target.value })
                }
                placeholder="Disk Capacity issues: 91% on host uk1puintser01"
              />
            </div>
            <div className="field">
              <label htmlFor="desc">Description</label>
              <textarea
                id="desc"
                rows="2"
                value={query.description}
                onChange={(e) =>
                  setQuery({ ...query, description: e.target.value })
                }
                placeholder="Additional context"
              />
            </div>
            <div className="field-row">
              <div className="field">
                <label htmlFor="cat">Category</label>
                <input
                  id="cat"
                  type="text"
                  value={query.category}
                  onChange={(e) =>
                    setQuery({ ...query, category: e.target.value })
                  }
                  placeholder="Events-CMK"
                />
              </div>
              <div className="field">
                <label htmlFor="grp">Assignment group</label>
                <input
                  id="grp"
                  type="text"
                  value={query.assignment_group}
                  onChange={(e) =>
                    setQuery({ ...query, assignment_group: e.target.value })
                  }
                  placeholder="Global Wintel Server Support"
                />
              </div>
            </div>
            <button type="submit" className="btn-primary" disabled={loading}>
              {loading ? "Searching…" : "Find resolutions"}
            </button>
          </form>

          {error && <div className="error-bar">{error}</div>}

          {/* Results */}
          {result ? (
            <div className="results">
              <div className="recommendation-card">
                <div className="recommendation-header">
                  <h3>Recommendation</h3>
                  <span
                    className={`confidence-pill confidence-${result.recommendation.confidence}`}
                  >
                    {result.recommendation.confidence}
                  </span>
                </div>
                <div className="recommendation-body">
                  {result.recommendation.summary}
                </div>
                <div className="recommendation-footer">
                  <span>
                    {result.recommendation.cited_tickets.length > 0 &&
                      `Based on ${result.recommendation.cited_tickets.join(", ")}`}
                  </span>
                  <code>{result.model}</code>
                </div>
              </div>

              <div className="similar-section">
                <h3>
                  Similar resolved tickets
                  <span className="result-count">
                    {result.retrieval_count} found
                  </span>
                </h3>
                {result.similar_tickets.length === 0 ? (
                  <p className="no-results">No similar tickets found.</p>
                ) : (
                  result.similar_tickets.map((t, i) => (
                    <div key={`${t.number}-${i}`} className="ticket-row">
                      <div className="ticket-row-head">
                        <span className="ticket-number">{t.number}</span>
                        <span className="match-pct">
                          {Math.round(t.similarity * 100)}%
                        </span>
                      </div>
                      <p>{t.short_description}</p>
                      {t.assignment_group && (
                        <span className="ticket-group">
                          {t.assignment_group}
                        </span>
                      )}
                      <details>
                        <summary>View resolution</summary>
                        <div className="resolution-text">{t.resolution}</div>
                      </details>
                    </div>
                  ))
                )}
              </div>
            </div>
          ) : !loading ? (
            <div className="main-empty">
              <div className="main-empty-icon">🔍</div>
              <p>Describe a ticket to find how similar issues were resolved.</p>
            </div>
          ) : null}
        </div>
      </div>

      {/* Status bar */}
      <div className="status-bar">
        <span className="status-dot" />
        <span>Ready</span>
        <button type="button" onClick={checkIndex}>
          Index status
        </button>
        {indexInfo && (
          <span>
            {indexInfo.indexed
              ? `${indexInfo.document_count} documents`
              : indexInfo.error || "Not indexed"}
          </span>
        )}
      </div>
    </div>
  );
}
