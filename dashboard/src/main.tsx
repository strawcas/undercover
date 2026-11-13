import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter } from "react-router";
import { App } from "./App";
import { AuthProvider } from "./AuthProvider";
import { applyTheme } from "./theme";
import "./styles.css";

const root = document.getElementById("root");
applyTheme();
if (!root) throw new Error("index.html has no #root element");

createRoot(root).render(
  <StrictMode>
    <BrowserRouter>
      <AuthProvider>
        <App />
      </AuthProvider>
    </BrowserRouter>
  </StrictMode>,
);
