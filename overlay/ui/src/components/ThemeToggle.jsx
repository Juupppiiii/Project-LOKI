import { useTheme } from "../theme.jsx";

/* Theme switch.
 *
 * A single button rather than a three-way control: click to flip, and the
 * tooltip says whether the current choice is explicit or inherited from the OS.
 * Right-click (context menu) is not hijacked; instead a small "follow system"
 * link appears once a manual choice has been made, so the OS default is
 * recoverable without hunting through settings.
 */

function SunIcon() {
  return (
    <svg viewBox="0 0 24 24" width="16" height="16" aria-hidden="true">
      <circle cx="12" cy="12" r="4.2" fill="currentColor" />
      <g
        stroke="currentColor"
        strokeWidth="1.8"
        strokeLinecap="round"
        fill="none"
      >
        <path d="M12 2.5v2.2M12 19.3v2.2M2.5 12h2.2M19.3 12h2.2" />
        <path d="M5.2 5.2l1.6 1.6M17.2 17.2l1.6 1.6M18.8 5.2l-1.6 1.6M6.8 17.2l-1.6 1.6" />
      </g>
    </svg>
  );
}

function MoonIcon() {
  return (
    <svg viewBox="0 0 24 24" width="16" height="16" aria-hidden="true">
      <path
        d="M20 14.2A8.2 8.2 0 1 1 9.8 4a6.6 6.6 0 0 0 10.2 10.2z"
        fill="currentColor"
      />
    </svg>
  );
}

export default function ThemeToggle() {
  const { theme, isDark, usingSystem, toggle, resetToSystem } = useTheme();

  const next = isDark ? "light" : "dark";
  const source = usingSystem
    ? "following your system setting"
    : "set manually for this browser";

  return (
    <div className="theme-toggle">
      <button
        type="button"
        className="btn btn-quiet btn-icon"
        onClick={toggle}
        title={`Switch to ${next} mode — currently ${theme}, ${source}`}
        aria-label={`Switch to ${next} mode`}
      >
        {isDark ? <MoonIcon /> : <SunIcon />}
        <span className="theme-label">{isDark ? "Dark" : "Light"}</span>
      </button>

      {!usingSystem ? (
        <button
          type="button"
          className="btn-link"
          onClick={resetToSystem}
          title="Go back to following your operating system setting"
        >
          use system
        </button>
      ) : null}
    </div>
  );
}
