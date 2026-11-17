import { defineConfig } from '@playwright/test';

const instance = process.env.OPENSHORTS_E2E_INSTANCE || `${Date.now()}-${process.pid}`;
process.env.OPENSHORTS_E2E_INSTANCE = instance;
const run = `${process.env.OPENSHORTS_E2E_RUN || 'run'}/${instance}`;

export default defineConfig({
  testDir: './e2e',
  timeout: 45 * 60_000,
  expect: { timeout: 30_000 },
  workers: 1,
  fullyParallel: false,
  retries: 0,
  outputDir: `../artifacts/e2e-2026-11-13/${run}/browser`,
  reporter: [['list'], ['json', { outputFile: `../artifacts/e2e-2026-11-13/${run}/results.json` }]],
  use: {
    baseURL: process.env.OPENSHORTS_E2E_URL || 'http://localhost:5175',
    viewport: { width: 1440, height: 1000 },
    actionTimeout: 30_000,
    navigationTimeout: 60_000,
    launchOptions: { executablePath: process.env.PLAYWRIGHT_CHROME_PATH || '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome' },
    screenshot: 'only-on-failure',
    trace: 'retain-on-failure',
    acceptDownloads: true,
  },
});
