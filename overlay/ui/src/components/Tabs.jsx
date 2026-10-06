import { useRef } from "react";

/* Tab bar with real keyboard support: arrow keys move between tabs, Home/End
 * jump to the ends. Uses the ARIA tablist pattern so screen readers announce
 * position ("tab 2 of 3") rather than reading three unrelated buttons.
 */

export default function Tabs({ tabs, active, onChange }) {
  const refs = useRef({});

  function onKeyDown(e) {
    const ids = tabs.map((t) => t.id);
    const i = ids.indexOf(active);
    let next = null;

    if (e.key === "ArrowRight") next = ids[(i + 1) % ids.length];
    else if (e.key === "ArrowLeft") next = ids[(i - 1 + ids.length) % ids.length];
    else if (e.key === "Home") next = ids[0];
    else if (e.key === "End") next = ids[ids.length - 1];
    else return;

    e.preventDefault();
    onChange(next);
    refs.current[next]?.focus();
  }

  return (
    <nav className="tabs" role="tablist" aria-label="Sections" onKeyDown={onKeyDown}>
      {tabs.map((t) => {
        const selected = t.id === active;
        return (
          <button
            key={t.id}
            ref={(el) => {
              refs.current[t.id] = el;
            }}
            type="button"
            role="tab"
            id={`tab-${t.id}`}
            aria-controls={`panel-${t.id}`}
            aria-selected={selected}
            tabIndex={selected ? 0 : -1}
            className="tab"
            onClick={() => onChange(t.id)}
          >
            {t.label}
            {t.count ? <span className="tab-count">{t.count}</span> : null}
          </button>
        );
      })}
    </nav>
  );
}
