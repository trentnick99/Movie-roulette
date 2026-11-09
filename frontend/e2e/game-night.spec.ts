import { expect, test, type Page } from "@playwright/test";

/*
  The acceptance workflow, driven through the real UI:
  configure TMDB -> add a folder -> scan -> fix an uncertain match -> players -> new game -> random movie ->
  timestamp -> play a real clip -> a phone joins -> guesses -> reveal -> points -> next round -> final results.
*/

const MOVIES = process.env.MR_E2E_MOVIES!;
const KEY = process.env.MR_E2E_TMDB_KEY ?? "e2e-test-key-0001";
const SHOTS = process.env.MR_E2E_SHOTS;

async function shot(page: Page, name: string) {
  if (SHOTS) await page.screenshot({ path: `${SHOTS}/${name}.png`, fullPage: false });
}

test("full game night", async ({ browser }) => {
  test.skip(!MOVIES, "MR_E2E_MOVIES not set");
  const hostCtx = await browser.newContext();
  const host = await hostCtx.newPage();
  const errors: string[] = [];
  host.on("pageerror", (e) => errors.push(e.message));

  // --- setup -------------------------------------------------------------------------------------------
  await host.goto("/");
  await expect(host.getByRole("heading", { name: /Point it at your movies/ })).toBeVisible();
  await shot(host, "01-home-empty");

  await host.goto("/settings?tab=tmdb");
  await host.getByLabel("TMDB API key").fill(KEY);
  await host.getByRole("button", { name: "Check and save" }).click();
  await expect(host.getByText("Key checked with TMDB and saved.")).toBeVisible();

  await host.goto("/settings?tab=library");
  await host.getByLabel("Ignore files shorter than (minutes)").fill("1");
  await host.getByLabel("Auto-accept matches at confidence").fill("70");
  await host.getByRole("button", { name: "Save changes" }).click();
  await expect(host.getByText("Settings saved.")).toBeVisible();
  await host.getByLabel("Folder path").fill(MOVIES);
  await host.getByRole("button", { name: "Add folder" }).click();
  await expect(host.getByText(MOVIES, { exact: true })).toBeVisible();
  await host.getByRole("button", { name: "Scan now" }).click();
  await expect(host.getByText(/Last scan completed/)).toBeVisible({ timeout: 90_000 });
  await shot(host, "02-settings-library");

  // --- library + manual match ------------------------------------------------------------------------
  await host.goto("/library");
  await expect(host.getByText("Iron Man 2", { exact: true })).toBeVisible();
  await shot(host, "03-library");
  await host.goto("/library?status=needs_review");
  await host.getByRole("button", { name: /The Thing/ }).first().click();
  await host.getByRole("button", { name: "Choose the right movie" }).click();
  const dialog = host.getByRole("dialog", { name: "Choose the right movie" });
  await expect(dialog.getByText("The Thing").first()).toBeVisible();
  await shot(host, "04-match");
  await dialog.locator("li", { hasText: "(1982)" }).getByRole("button", { name: "This is it" }).click();
  await expect(host.getByText("Match saved.")).toBeVisible();
  await host.keyboard.press("Escape");

  await host.goto("/categories");
  await expect(host.getByRole("heading", { name: "Genres" })).toBeVisible();
  await shot(host, "05-categories");

  // --- players -----------------------------------------------------------------------------------------
  await host.goto("/players");
  for (const name of ["Ana", "Ben"]) {
    await host.getByLabel("Player name").first().fill(name);
    await host.getByRole("button", { name: "Add player" }).click();
    await expect(host.locator(`input[value="${name}"]`)).toBeVisible();
  }

  // --- new game ------------------------------------------------------------------------------------------
  await host.goto("/play");
  await host.getByRole("button", { name: /Full Library Roulette/ }).click();
  await host.getByRole("button", { name: "15s" }).click();
  await expect(host.getByText(/movies can come up/)).toBeVisible();
  await shot(host, "06-setup");
  await host.getByRole("button", { name: "Create game" }).click();
  await expect(host).toHaveURL(/\/game\/\d+/);
  const code = (await host.locator("p.num.title").first().textContent())!.trim();
  expect(code).toMatch(/^[A-Z0-9]{6}$/);

  // a phone joins
  const phoneCtx = await browser.newContext({ viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true });
  const phone = await phoneCtx.newPage();
  phone.on("pageerror", (e) => errors.push(e.message));
  await phone.goto(`/join?code=${code}`);
  await phone.getByLabel("Your name").fill("Ana");
  await phone.getByRole("button", { name: "Join" }).click();
  await expect(phone.getByText("Waiting for the next round")).toBeVisible();
  await shot(phone, "07-phone-waiting");

  // --- round 1: random movie, the phone picks a random timestamp -----------------------------------------
  await host.getByRole("button", { name: "Spin the first round" }).click();
  await expect(host.getByRole("heading", { name: "Choose where the scene starts" })).toBeVisible();
  await host.getByRole("button", { name: "Show answer to me" }).click();
  const answer = (await host.locator("p.title.text-xl").first().textContent())!.replace(/\s*\(\d{4}\)$/, "").trim();
  await host.getByRole("button", { name: "Hide answer" }).click();
  await shot(host, "08-host-timestamp");

  // spoiler protection on the phone: no title anywhere in the page or its title
  await expect(phone.getByRole("heading", { name: "Your turn: choose where the scene starts" })).toBeVisible();
  expect(await phone.content()).not.toContain(answer);
  expect(await phone.title()).toBe("Movie Roulette");
  await phone.getByRole("tab", { name: "Random time" }).click();
  await phone.getByRole("button", { name: "Generate a random time" }).click();
  await phone.getByRole("button", { name: "Use this time" }).click();
  await shot(phone, "09-phone-picked");

  // host plays the real clip once FFmpeg has prepared it
  const play = host.getByRole("button", { name: "Play the scene" });
  await expect(play).toBeEnabled({ timeout: 60_000 });
  await play.click();
  await expect(host.getByRole("button", { name: "Pause" })).toBeVisible();
  // the video element really advances
  await expect
    .poll(async () => host.locator("video").evaluate((v: HTMLVideoElement) => v.currentTime), { timeout: 30_000 })
    .toBeGreaterThan(1);
  await shot(host, "10-host-playing");
  expect(await phone.content()).not.toContain(answer);

  // the phone guesses
  await phone.getByLabel("Your guess").fill(answer);
  await phone.getByRole("button", { name: "Guess" }).click();
  await expect(phone.getByText("waiting for the host")).toBeVisible();
  await shot(phone, "11-phone-guessed");

  await host.getByRole("button", { name: "Correct" }).first().click();
  await expect(phone.getByText("Correct. Wait for the reveal.")).toBeVisible();
  await host.getByRole("button", { name: "Reveal the movie" }).click();
  await expect(host.getByRole("heading", { name: answer })).toBeVisible();
  await expect(phone.getByRole("heading", { name: answer })).toBeVisible();
  await shot(host, "12-host-reveal");
  await shot(phone, "13-phone-reveal");
  await host.getByRole("button", { name: "Knew the year" }).isDisabled();
  await host.getByLabel("Player").selectOption({ label: "Ana" });
  await host.getByRole("button", { name: "Knew the year" }).click();
  await expect(host.getByText("Year bonus given.")).toBeVisible();
  await host.getByRole("button", { name: "Close round" }).click();

  // --- round 2: host picks a manual time, Ben answers aloud -----------------------------------------------
  await host.getByRole("button", { name: "Spin the next round" }).click();
  await expect(host.getByRole("heading", { name: "Choose where the scene starts" })).toBeVisible();
  await host.getByLabel("Or type a time").fill("0:00:30");
  await host.getByRole("button", { name: "Start at 0:00:30" }).click();
  await expect(host.getByRole("button", { name: "Play the scene" })).toBeEnabled({ timeout: 60_000 });
  await host.getByLabel("Someone said it out loud").selectOption({ label: "Ben" });
  await host.getByRole("button", { name: "Mark correct" }).click();
  await host.getByRole("button", { name: "Reveal the movie" }).click();
  await host.getByRole("button", { name: "Close round" }).click();

  // --- finish ------------------------------------------------------------------------------------------
  await host.getByRole("button", { name: "Finish game" }).click();
  await host.getByRole("button", { name: "Finish and show results" }).click();
  await expect(host.getByText("Final results")).toBeVisible();
  await expect(phone.getByText("Final results")).toBeVisible();
  await shot(host, "14-final");
  await shot(phone, "15-phone-final");

  await host.goto("/history");
  await host.getByText(/Won by/).first().click();
  await expect(host.getByText(/Round 1/)).toBeVisible();
  await host.goto("/stats");
  await expect(host.getByRole("heading", { name: "Players" })).toBeVisible();
  await shot(host, "16-stats");
  await host.goto("/");
  await shot(host, "17-home");

  // the phone cannot reach host screens or host APIs
  const res = await phone.request.get("/api/status");
  expect(res.status()).toBe(401);
  expect(errors).toEqual([]);
});
