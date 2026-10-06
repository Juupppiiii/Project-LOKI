import { createContext, useCallback, useContext, useEffect, useState } from "react";

/* Theme handling.
 *
 * Three behaviours worth noting:
 *   1. The initial theme is applied by an inline script in index.html, before
 *      React mounts, so there is no flash of the wrong colours on load.
 *   2. An explicit choice is remembered in localStorage.
 *   3. Until someone chooses, the OS preference wins -- and keeps winning if the
 *      OS flips (a laptop switching to dark at sunset, for example).
 */

const STORAGE_KEY = "overlay-theme";
const ThemeContext = createContext(null);

function systemTheme() {
  return window.matchMedia?.("(prefers-color-scheme: dark)").matches
    ? "dark"
    : "light";
}

function storedTheme() {
  try {
    const v = localStorage.getItem(STORAGE_KEY);
    return v === "dark" || v === "light" ? v : null;
  } catch {
    return null; // private mode or blocked storage
  }
}

export function ThemeProvider({ children }) {
  const [explicit, setExplicit] = useState(storedTheme);
  const [system, setSystem] = useState(systemTheme);

  const theme = explicit ?? system;

  // Track the OS preference so an unset theme follows it live.
  useEffect(() => {
    const mq = window.matchMedia?.("(prefers-color-scheme: dark)");
    if (!mq) return;
    const onChange = (e) => setSystem(e.matches ? "dark" : "light");
    mq.addEventListener("change", onChange);
    return () => mq.removeEventListener("change", onChange);
  }, []);

  useEffect(() => {
    document.documentElement.dataset.theme = theme;
  }, [theme]);

  const setTheme = useCallback((next) => {
    setExplicit(next);
    try {
      if (next === null) localStorage.removeItem(STORAGE_KEY);
      else localStorage.setItem(STORAGE_KEY, next);
    } catch {
      /* storage unavailable; the theme still applies for this session */
    }
  }, []);

  const toggle = useCallback(
    () => setTheme(theme === "dark" ? "light" : "dark"),
    [theme, setTheme]
  );

  return (
    <ThemeContext.Provider
      value={{
        theme,
        isDark: theme === "dark",
        usingSystem: explicit === null,
        setTheme,
        toggle,
        resetToSystem: () => setTheme(null),
      }}
    >
      {children}
    </ThemeContext.Provider>
  );
}

export function useTheme() {
  const ctx = useContext(ThemeContext);
  if (!ctx) throw new Error("useTheme must be used inside ThemeProvider");
  return ctx;
}
