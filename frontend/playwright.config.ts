import { defineConfig } from "@playwright/test";

/*
  Browser end-to-end tests. They expect a running Movie Roulette server (default http://127.0.0.1:8765) that uses
  the fake TMDB server from backend/tests/fake_tmdb_server.py, and a folder of test videos in MR_E2E_MOVIES.
  scripts/e2e.sh sets all of this up. CHROMIUM_PATH can point at a system Chromium.
*/
export default defineConfig({
  testDir: "./e2e",
  timeout: 180_000,
  expect: { timeout: 20_000 },
  workers: 1,
  reporter: [["list"]],
  use: {
    baseURL: process.env.MR_E2E_URL ?? "http://127.0.0.1:8765",
    launchOptions: process.env.CHROMIUM_PATH
      ? { executablePath: process.env.CHROMIUM_PATH, args: ["--no-sandbox", "--autoplay-policy=no-user-gesture-required"] }
      : { args: ["--autoplay-policy=no-user-gesture-required"] },
    screenshot: "only-on-failure",
    viewport: { width: 1440, height: 900 },
  },
});
