/* Filtering controls.
 *
 * Filtering and sorting are presentation, so they belong here and run in the
 * browser. Policy (what may be approved or run) stays on the server.
 */

/** Segmented control with counts, e.g. Needs you / Running / Problems. */
export function Segmented({ options, value, onChange, counts = {}, label }) {
  return (
    <div className="seg-group" role="group" aria-label={label}>
      {options.map((o) => (
        <button
          key={o.id}
          type="button"
          className="seg"
          aria-pressed={value === o.id}
          title={o.help}
          onClick={() => onChange(o.id)}
        >
          {o.label}
          <span className="seg-count">{counts[o.id] ?? 0}</span>
        </button>
      ))}
    </div>
  );
}

export function SearchInput({ value, onChange, placeholder, label }) {
  return (
    <div className="search-wrap">
      <svg
        className="search-icon"
        viewBox="0 0 24 24"
        width="14"
        height="14"
        aria-hidden="true"
      >
        <circle
          cx="10.5"
          cy="10.5"
          r="6.5"
          fill="none"
          stroke="currentColor"
          strokeWidth="2"
        />
        <path
          d="M15.5 15.5L21 21"
          stroke="currentColor"
          strokeWidth="2"
          strokeLinecap="round"
        />
      </svg>
      <input
        type="search"
        className="input search-input"
        value={value}
        placeholder={placeholder}
        aria-label={label || placeholder}
        onChange={(e) => onChange(e.target.value)}
      />
      {value ? (
        <button
          type="button"
          className="search-clear"
          onClick={() => onChange("")}
          aria-label="Clear search"
          title="Clear"
        >
          ×
        </button>
      ) : null}
    </div>
  );
}

/** A select whose options are derived from the data, with counts. */
export function FacetSelect({ value, onChange, options, allLabel, label }) {
  return (
    <select
      className="select select-compact"
      value={value}
      aria-label={label}
      onChange={(e) => onChange(e.target.value)}
    >
      <option value="">{allLabel}</option>
      {options.map((o) => (
        <option key={o.value} value={o.value}>
          {o.label}
          {o.count != null ? ` (${o.count})` : ""}
        </option>
      ))}
    </select>
  );
}

export function SortSelect({ value, onChange, options }) {
  return (
    <select
      className="select select-compact"
      value={value}
      aria-label="Sort order"
      onChange={(e) => onChange(e.target.value)}
    >
      {options.map((o) => (
        <option key={o.value} value={o.value}>
          {o.label}
        </option>
      ))}
    </select>
  );
}

/** Counts how many items fall under each value of a key, for facet options. */
export function facetOptions(items, getValue) {
  const counts = new Map();
  for (const item of items) {
    const v = getValue(item);
    if (!v) continue;
    counts.set(v, (counts.get(v) || 0) + 1);
  }
  return [...counts.entries()]
    .sort((a, b) => a[0].localeCompare(b[0]))
    .map(([value, count]) => ({ value, label: value, count }));
}
