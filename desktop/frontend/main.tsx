import React from "react";
import ReactDOM from "react-dom/client";
import { HashRouter } from "react-router-dom";
import App from "./App";
import { NoorProvider } from "./hooks/useNoor";
import "./index.css";

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <HashRouter>
      <NoorProvider>
        <App />
      </NoorProvider>
    </HashRouter>
  </React.StrictMode>,
);
