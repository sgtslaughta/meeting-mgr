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
});
