import { test, expect } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';

test('download all exports the completed real project as a ZIP', async ({ page }) => {
  test.skip(process.env.OPENSHORTS_REAL_E2E !== '1', 'Requires completed real source generation.');
  const evidence = path.resolve('../artifacts/e2e-2026-11-13');
  const initial = JSON.parse(fs.readFileSync(path.join(evidence, 'generation-result.json')));
  const title = initial.result.clips[0].video_title_for_youtube_short;
  const escaped = title.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  await page.goto('/#app');
  await page.getByRole('button', { name: new RegExp(`^${escaped}`) }).click();
  await expect(page.getByRole('heading', { name: /Generated shorts/ })).toBeVisible();
  const pending = page.waitForEvent('download', { timeout: 180_000 });
  await page.getByRole('button', { name: 'Download all', exact: true }).click();
  const zip = await pending;
  await zip.saveAs(path.join(evidence, 'all-edited-clips.zip'));
  expect(await zip.failure()).toBeNull();
  expect(fs.statSync(path.join(evidence, 'all-edited-clips.zip')).size).toBeGreaterThan(1000);
  await page.screenshot({ path: path.join(evidence, 'download-all-complete.png'), fullPage: true });
});
