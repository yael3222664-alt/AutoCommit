import { defineConfig } from "vitest/config";

// Runs the frontend client and store against a real backend process (see integration/backend.ts).
export default defineConfig({
  test: {
    environment: "node",
    include: ["integration/**/*.test.ts"],
    testTimeout: 60_000,
    hookTimeout: 60_000,
    fileParallelism: false,
  },
});
