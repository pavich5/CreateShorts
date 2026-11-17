import { test, expect } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';

const evidence = path.resolve('../artifacts/e2e-2026-11-13', process.env.OPENSHORTS_GENERATION_LABEL || '.');
const source = process.env.OPENSHORTS_REAL_TEST_VIDEO || path.join(process.env.HOME, 'Downloads/Alex Hormozi The 4 Steps To Your First Dollars Tomorrow.mp4');

test('real source upload and 9:16 generation', async ({ page }) => {
  test.setTimeout(90 * 60_000);
  test.skip(process.env.OPENSHORTS_REAL_E2E !== '1', 'Opt in: real video processing and signed-in AI account usage.');
  expect(fs.statSync(source).size).toBeGreaterThan(0);
  fs.mkdirSync(evidence, { recursive: true });
  const events = [];
  page.on('console', msg => { if (['error', 'warning'].includes(msg.type())) events.push({ type: msg.type(), text: msg.text() }); });
  page.on('pageerror', error => events.push({ type: 'pageerror', text: error.message }));
  page.on('response', response => {
    if (response.url().includes('/api/') && response.status() >= 400) events.push({ type: 'http', status: response.status(), url: response.url() });
  });
  try {
    await page.goto('/#app');
    await expect(page.getByRole('heading', { name: 'Turn a long video into great shorts.' })).toBeVisible();
    await expect(page.getByRole('button', { name: 'Generate Clips', exact: true })).toBeDisabled();
    await page.getByRole('button', { name: 'Video URL', exact: true }).click();
    await page.getByRole('button', { name: 'Supported platforms' }).click();
    await expect(page.getByText('Vimeo', { exact: true })).toBeVisible();
    await page.getByRole('button', { name: 'Supported platforms' }).click();
    await page.getByRole('button', { name: 'Upload File', exact: true }).click();
    await page.getByLabel('Category name', { exact: true }).fill(process.env.OPENSHORTS_GENERATION_CATEGORY || 'E2E Alex Hormozi 2026-11-13');
    await page.locator('input[type=file]').setInputFiles(source);
    await expect(page.getByText(path.basename(source), { exact: true })).toBeVisible();
    await page.getByRole('button', { name: '9:16 Shorts · Reels · TikTok', exact: true }).click();
    await page.getByLabel('How many clips do you want?', { exact: true }).fill(process.env.OPENSHORTS_GENERATION_COUNT || '3');
    await page.locator('label.caption-style-choice:has(input[value="cc_mozi"])').click();
    await page.getByRole('button', { name: 'advanced options', exact: true }).click();
    const numbers = page.locator('input[type=number]');
    await numbers.nth(1).fill(process.env.OPENSHORTS_GENERATION_MIN || '18');
    await numbers.nth(2).fill(process.env.OPENSHORTS_GENERATION_MAX || '30');
    if (process.env.OPENSHORTS_GENERATION_LAYOUT) await page.getByLabel('vertical layout').selectOption(process.env.OPENSHORTS_GENERATION_LAYOUT);
    await page.screenshot({ path: path.join(evidence, 'source-upload-configured.png'), fullPage: true });
    const processResponse = page.waitForResponse(r => r.url().endsWith('/api/process') && r.request().method() === 'POST', { timeout: 120_000 });
    await page.getByRole('button', { name: 'Generate Clips', exact: true }).click();
    const response = await processResponse;
    const submitted = await response.json();
    fs.writeFileSync(path.join(evidence, 'generation-submission.json'), JSON.stringify({ status: response.status(), submitted }, null, 2));
    expect(response.ok(), JSON.stringify(submitted)).toBeTruthy();
    const jobId = submitted.job_id;
    expect(jobId).toBeTruthy();
    console.log(`REAL_GENERATION_JOB=${jobId}`);
    fs.writeFileSync(path.join(evidence, 'job.json'), JSON.stringify({ jobId, source, submitted }, null, 2));
    const terminal = await page.waitForResponse(async r => {
      if (!r.url().includes(`/api/status/${jobId}`)) return false;
      const status = await r.json().catch(() => ({}));
      return ['completed', 'failed', 'error', 'cancelled'].includes(status.status);
    }, { timeout: 80 * 60_000 });
    const result = await terminal.json();
    fs.writeFileSync(path.join(evidence, 'generation-result.json'), JSON.stringify(result, null, 2));
    expect(result.status, JSON.stringify(result)).toBe('completed');
    await page.screenshot({ path: path.join(evidence, 'generated-results.png'), fullPage: true });
    fs.writeFileSync(path.join(evidence, 'results-dom.txt'), await page.locator('body').ariaSnapshot());
    await page.context().storageState({ path: path.join(evidence, 'browser-state.json') });
    expect(result.result?.clips?.length || result.clips?.length).toBeGreaterThan(0);
  } finally {
    fs.writeFileSync(path.join(evidence, 'browser-events.json'), JSON.stringify(events, null, 2));
  }
});
