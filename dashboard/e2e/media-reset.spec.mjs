import { test, expect } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';

test('clear prior test media and prepare a short real clip for lifecycle regression', async ({ page }) => {
  test.skip(process.env.OPENSHORTS_REAL_E2E !== '1', 'Changes only the dedicated primary test project.');
  const evidence = path.resolve('../artifacts/e2e-2026-11-13');
  const jobId = JSON.parse(fs.readFileSync(path.join(evidence, 'job.json'))).jobId;
  await page.goto(`/#edit/${jobId}/0`);
  await expect.poll(async () => Number(await page.getByLabel('Trim end in seconds').inputValue())).toBeGreaterThan(0);
  await page.locator('#edit-tab-media').click();
  const clear = page.getByRole('button', { name: 'Remove all media', exact: true });
  if (await clear.isVisible()) {
    await clear.click();
    const response = page.waitForResponse(r => new URL(r.url()).pathname === `/api/clip/${jobId}/0/broll` && r.request().method() === 'PUT', { timeout: 600_000 });
    await page.getByRole('button', { name: 'Save changes', exact: true }).click();
    const saved = await response;
    expect(saved.ok(), await saved.text()).toBeTruthy();
    await expect(page.locator('.unified-editor-page-state')).toHaveText('Ready to edit');
  }
  await page.locator('#edit-tab-trim').click();
  await expect.poll(async () => Number(await page.getByLabel('Trim end in seconds').inputValue())).toBeGreaterThan(0);
  if (Number(await page.getByLabel('Trim end in seconds').inputValue()) > 6.1) {
    await page.getByLabel('Trim end in seconds').fill('6');
    const response = page.waitForResponse(r => r.url().endsWith('/api/edit/trim') && r.request().method() === 'POST', { timeout: 600_000 });
    await page.getByRole('button', { name: 'Apply trim', exact: true }).click();
    const saved = await response;
    expect(saved.ok(), await saved.text()).toBeTruthy();
    await expect(page.locator('.unified-editor-page-state')).toHaveText('Ready to edit');
  }
  await page.screenshot({ path: path.join(evidence, 'media-lifecycle-reset.png'), fullPage: true });
});
