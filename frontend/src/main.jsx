import React from "react";
import ReactDOM from "react-dom/client";
import App from "./App.jsx";
import SharedView from "./components/SharedView.jsx";
import { applyTheme, settingsStore } from "./lib/storage.js";
import "./styles.css";

// Apply the saved theme before the first paint so the page doesn't flash.
applyTheme(settingsStore.load().theme);

// /share/<id> is the public read-only page for a shared chat; everything else is the app.
const shared = window.location.pathname.match(/^\/share\/([A-Za-z0-9_-]{10,40})\/?$/);

ReactDOM.createRoot(document.getElementById("root")).render(
  <React.StrictMode>
    {shared ? <SharedView shareId={shared[1]} /> : <App />}
  </React.StrictMode>
);
