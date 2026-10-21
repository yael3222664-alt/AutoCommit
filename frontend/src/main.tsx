import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import { App } from "./App";
import "./styles.css";
import { RecordStoreProvider } from "./context/RecordStoreContext";

const root = document.getElementById("root");
if (!root) throw new Error("Root element #root not found");

createRoot(root).render(
  <StrictMode>
    <RecordStoreProvider>
      <App />
    </RecordStoreProvider>
  </StrictMode>,
);
