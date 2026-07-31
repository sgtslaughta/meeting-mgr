// web/e2e/review.spec.ts
import { expect, test } from "@playwright/test";
import { execSync } from "node:child_process";
import { mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

test("upload, watch progress, confirm a speaker", async ({ page }) => {
  const dir = mkdtempSync(join(tmpdir(), "mm-"));
  const wav = join(dir, "tone.wav");
  execSync(`ffmpeg -y -f lavfi -i sine=frequency=440:duration=3 ${wav}`,
           { stdio: "ignore" });

  await page.goto("/");
  await page.getByPlaceholder("title").fill("e2e standup");
  await page.locator('input[type="file"]').setInputFiles(wav);
  await page.getByRole("button", { name: "Upload" }).click();

  await page.getByRole("link", { name: "e2e standup" }).click();

  // SSE drives this; if it never arrives the test fails here rather than
  // silently passing against a stale page.
  await expect(page.locator("audio#player")).toBeVisible();
  await expect(page.getByText(/published|Speakers/)).toBeVisible({ timeout: 120_000 });

  const nameInput = page.getByPlaceholder("who is this?").first();
  await nameInput.fill("Sarah Chen");
  await page.getByRole("button", { name: "Confirm" }).first().click();

  await expect(page.locator("[data-provenance='confirmed']").first())
    .toBeVisible();

  // The badge above is backed by a fresh getMeeting() today, so it already
  // proves the server-side state -- but only as long as nothing adds
  // optimistic client updates. Assert on the API's own JSON directly so a
  // future optimistic-UI refactor can't silently defeat this test.
  const meetingId = page.url().match(/\/meetings\/(\d+)/)?.[1];
  const body = await (await page.request.get(`/meetings/${meetingId}`)).json();
  expect(body.attributions.some(
    (a: { provenance: string }) => a.provenance === "confirmed")).toBe(true);
});
