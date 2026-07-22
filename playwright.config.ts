import { defineConfig, devices } from "@playwright/test";

const httpsPort = 3443;

export default defineConfig({
  testDir: "./tests/e2e",
  globalTeardown: "./tests/e2e/global-teardown.mjs",
  fullyParallel: false,
  forbidOnly: true,
  retries: process.env.CI === undefined ? 0 : 1,
  workers: 1,
  reporter: [["line"], ["html", { outputFolder: "artifacts/e2e/playwright-report", open: "never" }]],
  outputDir: "artifacts/e2e/test-results",
  use: {
    baseURL: `https://localhost:${httpsPort}`,
    ignoreHTTPSErrors: true,
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
    video: "retain-on-failure",
  },
  projects: [
    { name: "desktop-chromium", grepInvert: /E2E-004/, use: { ...devices["Desktop Chrome"], viewport: { width: 1440, height: 900 } } },
    { name: "mobile-chromium", grep: /(?:E2E-001|E2E-005|UI-)/, use: { ...devices["Desktop Chrome"], viewport: { width: 360, height: 800 }, isMobile: true, hasTouch: true } },
    { name: "operations-chromium", grep: /E2E-004/, use: { ...devices["Desktop Chrome"], viewport: { width: 1440, height: 900 } } },
  ],
  webServer: {
    command: "node tests/e2e/https-web-server.mjs",
    url: `https://localhost:${httpsPort}`,
    ignoreHTTPSErrors: true,
    reuseExistingServer: false,
    timeout: 120_000,
  },
});
