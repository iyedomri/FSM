import { defineConfig } from "vitest/config";

export default defineConfig({
  test: {
    environment: "node",
    include: ["lib/**/*.test.ts"],
    maxWorkers: 1,
    restoreMocks: true,
    unstubGlobals: true
  }
});
