import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import { App } from "./App";
import { AuthGate } from "./components/AuthGate";
import "./styles.css";
import { RecordStoreProvider } from "./context/RecordStoreContext";

const root = document.getElementById("root");
if (!root) throw new Error("Root element #root not found");

createRoot(root).render(
  <StrictMode>
    <AuthGate>
      <RecordStoreProvider>
        <App />
      </RecordStoreProvider>
    </AuthGate>
  </StrictMode>,
);
