import { useState } from "react";

const API = "";

export default function App() {
  const [query, setQuery] = useState({
    short_description: "",
    description: "",
    category: "",
    assignment_group: "",
  });
  const [result, setResult] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [indexStatus, setIndexStatus] = useState(null);

  async function handleSubmit(e) {
    e.preventDefault();
    if (!query.short_description.trim()) return;
    setLoading(true);
    setError(null);
    setResult(null);
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
      setResult(await res.json());
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }

  async function checkIndex() {
    try {
      const res = await fetch(`${API}/api/index/status`);
      setIndexStatus(await res.json());
    } catch (err) {
      setIndexStatus({ error: err.message });
    }
  }

  return (
    <div className="app">
      <header>
        <h1>Bucky</h1>
        <p className="subtitle">IT Ticket Resolution Assistant</p>
      </header>

      <main>
        <section className="query-section">
          <h2>Describe the ticket</h2>
          <form onSubmit={handleSubmit}>
            <div className="field">
              <label htmlFor="short_desc">Short description *</label>
              <input
                id="short_desc"
                type="text"
                value={query.short_description}
                onChange={(e) =>
                  setQuery({ ...query, short_description: e.target.value })
                }
                placeholder="e.g. Disk Capacity issues: 91% on host uk1puintser01"
                required
              />
            </div>
            <div className="field">
              <label htmlFor="desc">Description</label>
              <textarea
                id="desc"
                rows="3"
                value={query.description}
                onChange={(e) =>
                  setQuery({ ...query, description: e.target.value })
                }
                placeholder="Additional details (optional)"
              />
            </div>
            <div className="field-row">
              <div className="field">
                <label htmlFor="category">Category</label>
                <input
                  id="category"
                  type="text"
                  value={query.category}
                  onChange={(e) =>
                    setQuery({ ...query, category: e.target.value })
                  }
                  placeholder="e.g. Events-CMK"
                />
              </div>
              <div className="field">
                <label htmlFor="group">Assignment group</label>
                <input
                  id="group"
                  type="text"
                  value={query.assignment_group}
                  onChange={(e) =>
                    setQuery({ ...query, assignment_group: e.target.value })
                  }
                  placeholder="e.g. Global Wintel Server Support"
                />
              </div>
            </div>
            <button type="submit" className="btn-primary" disabled={loading}>
              {loading ? "Searching…" : "Find resolutions"}
            </button>
          </form>
        </section>

        {error && <div className="error">{error}</div>}

        {result && (
          <>
            <section className="recommendation">
              <h2>
                AI Recommendation
                <span className={`confidence confidence-${result.recommendation.confidence}`}>
                  {result.recommendation.confidence} confidence
                </span>
              </h2>
              <div className="recommendation-text">
                {result.recommendation.summary}
              </div>
              {result.recommendation.cited_tickets.length > 0 && (
                <p className="cited">
                  Based on: {result.recommendation.cited_tickets.join(", ")}
                </p>
              )}
              <p className="model-tag">Model: {result.model}</p>
            </section>

            <section className="similar-tickets">
              <h2>
                Similar resolved tickets
                <span className="count">{result.retrieval_count} found</span>
              </h2>
              {result.similar_tickets.length === 0 ? (
                <p className="empty">
                  No similar tickets found. Try broadening the description.
                </p>
              ) : (
                <div className="ticket-list">
                  {result.similar_tickets.map((t, i) => (
                    <div key={`${t.number}-${i}`} className="ticket-card">
                      <div className="ticket-header">
                        <strong>{t.number}</strong>
                        <span className="similarity">
                          {Math.round(t.similarity * 100)}% match
                        </span>
                      </div>
                      <p className="ticket-desc">{t.short_description}</p>
                      {t.assignment_group && (
                        <span className="ticket-group">{t.assignment_group}</span>
                      )}
                      <details>
                        <summary>View resolution</summary>
                        <p className="resolution-text">{t.resolution}</p>
                      </details>
                    </div>
                  ))}
                </div>
              )}
            </section>
          </>
        )}
      </main>

      <footer>
        <button type="button" className="btn-link" onClick={checkIndex}>
          Check index status
        </button>
        {indexStatus && (
          <span className="index-info">
            {indexStatus.indexed
              ? `${indexStatus.document_count} documents indexed`
              : indexStatus.error || "Not indexed"}
          </span>
        )}
      </footer>
    </div>
  );
}
