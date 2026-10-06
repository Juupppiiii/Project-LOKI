# Overlay UI (React)

The front end for the task overlay. Holds **no business logic** — it renders what
the API returns and calls the API back. Every decision about what may be
approved or run stays server-side, so a tampered client gains nothing.

## Run it

```powershell
# one-off build, then the Python server serves it at http://127.0.0.1:8800
cd overlay/ui
npm install
npm run build
```

```powershell
# or develop with hot reload on http://127.0.0.1:5173
# (needs the Python API running on 8800 -- vite proxies /api to it)
npm run dev
```

FastAPI prefers `overlay/ui/dist` when it exists and falls back to the original
dependency-free client at `/legacy`. `GET /api/ui-info` says which one is being
served, which is the quickest way to spot a stale or missing build.

## This network needs the internal registry

The public npm registry is blocked here (`403 Forbidden`). `.npmrc` in this
folder points at the internal Nexus mirror instead:

```
registry=https://nexus.tpicapcloud.com/repository/npm-group/
```

The user-level `~/.npmrc` is left alone; its `cafile` (corporate TLS bundle)
still applies because npm merges project and user config.

**On `npm audit`:** Nexus IQ reports findings against `react` and
`source-map-js`. Do not run `npm audit fix --force` — it proposes
`react-dom@0.1.0` and `vite@2.3.6`, which would break the app. The `react`
finding cites a 2017 advisory against every version, which does not hold up. The
`source-map-js` finding reaches the tree through `postcss` → `vite`, which is
build tooling: it does not ship in the bundle served to users.

## Layout

```
src/
  main.jsx           mounts React, imports the stylesheets
  App.jsx            shell: identity, tabs, shared run list, footer facts
  api.js             the only module that talks to the server
  theme.jsx          light/dark with persistence and OS following
  vocabulary.js      server codes -> plain words (single source of wording)
  components/
    Tabs.jsx         ARIA tablist with arrow-key navigation
    ThemeToggle.jsx  sun/moon switch plus "use system"
    FilterBar.jsx    segmented counts, search, facet selects
    primitives.jsx   Badge, RiskBadge, StateTag, Progress, Callout, Panel, ...
  views/
    QueueView.jsx    filtered queue + intake
    RunDetail.jsx    next action, parameters, plan, history
    KnowledgeView.jsx uploads, draft review, publish, dictionary
    LiveView.jsx     feed settings and the ticket inbox
  styles/
    tokens.css       design tokens; both themes live here
    base.css         elements and shared classes
    components.css   component styles
```

## Theming

Colours are named by **role** (`--surface`, `--ink`, `--danger`), never by hue, so
a theme is just a different set of values for the same names. Components never
reference a raw colour, which is what makes the switch a one-liner.

Dark mode is re-picked rather than inverted: surfaces are lifted with lightness
instead of going to pure black, and accents are desaturated so they do not
vibrate against dark backgrounds.

Three behaviours worth knowing:

1. An inline script in `index.html` applies the saved theme **before** React
   mounts, so there is no flash of the wrong colours.
2. An explicit choice persists in `localStorage`.
3. Until someone chooses, the OS preference wins — and keeps winning if the OS
   flips later. Once a manual choice is made, a "use system" link appears so the
   default is recoverable.

## Accessibility

- Colour is never the only signal: every risk and state badge carries words.
- Tabs implement the ARIA tablist pattern with arrow, Home and End keys.
- Focus is always visible, one consistent ring everywhere.
- `prefers-reduced-motion` disables transitions and the spinner animation.
- `color-scheme` is set per theme so native controls and scrollbars follow.

## Pointing it at a remote server

Set one global before the bundle loads — the only change a desktop shell
(Tauri, Electron) needs:

```html
<script>window.OVERLAY_API_BASE = "https://overlay.internal.example.com";</script>
```

Then allow that origin on the server via `OVERLAY_ALLOWED_ORIGINS`.
