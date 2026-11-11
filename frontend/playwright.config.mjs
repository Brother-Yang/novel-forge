import { defineConfig, devices } from "@playwright/test";

export default defineConfig({
  testDir: "./e2e",
  testMatch: "**/*.e2e.mjs",
  workers: 1,
  forbidOnly: true,
  timeout: 45_000,
  expect: { timeout: 8000 },
  use: {
    ...devices["Desktop Chrome"],
    viewport: { width: 1440, height: 900 },
    actionTimeout: 10_000,
    acceptDownloads: true,
    serviceWorkers: "block",
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
});
