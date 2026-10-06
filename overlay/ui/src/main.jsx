import React from "react";
import { createRoot } from "react-dom/client";

import App from "./App.jsx";
import { ThemeProvider } from "./theme.jsx";

import "./styles/tokens.css";
import "./styles/base.css";

createRoot(document.getElementById("root")).render(
  <React.StrictMode>
    <ThemeProvider>
      <App />
    </ThemeProvider>
  </React.StrictMode>
);
