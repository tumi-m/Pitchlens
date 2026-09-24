import { defineConfig } from '@playwright/test';
export default defineConfig({
  testDir: './tests', timeout: 60_000, workers: 1,
  // In CI, failures become check annotations readable without the raw log.
  reporter: process.env.CI ? [['github'], ['list']] : 'list',
  use: { baseURL: 'http://127.0.0.1:3000', headless: true, ...(process.env.PLAYWRIGHT_CHANNEL ? { channel: process.env.PLAYWRIGHT_CHANNEL } : {}), screenshot: 'only-on-failure' },
  webServer: { command: 'npm run start -- --hostname 127.0.0.1', url: 'http://127.0.0.1:3000', reuseExistingServer: !process.env.CI, timeout: 120_000 },
});
