import { defineConfig } from "vitest/config";

// Drives the real UI in headless Chrome against the Vite dev server and a real backend
// (see acceptance/acceptance.test.ts for requirements and environment variables).
export default defineConfig({
  test: {
    environment: "node",
    include: ["acceptance/**/*.test.ts"],
    testTimeout: 120_000,
    hookTimeout: 120_000,
    fileParallelism: false,
  },
});
