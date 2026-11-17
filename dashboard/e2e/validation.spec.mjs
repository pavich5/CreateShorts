import { test, expect } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';

const root = path.resolve('../artifacts/e2e-2026-11-13');
const run = process.env.OPENSHORTS_E2E_RUN || 'validation';
const evidence = path.join(root, run);
const jobFile = path.join(root, 'job.json');
const jobId = process.env.OPENSHORTS_E2E_JOB || (fs.existsSync(jobFile) ? JSON.parse(fs.readFileSync(jobFile)).jobId : null);
const shortSource = process.env.OPENSHORTS_SHORT_TEST_VIDEO || path.join(root, 'validation-source-6s.mp4');

test.beforeEach(() => {
  test.skip(process.env.OPENSHORTS_REAL_E2E !== '1', 'Real source/clip validation opt-in.');
  test.setTimeout(180_000);
  fs.mkdirSync(evidence, { recursive: true });
});

test('the real six-second 9:16 source is rejected before processing', async ({ page }) => {
  expect(fs.statSync(shortSource).size).toBeGreaterThan(0);
  const submissions = [];
  page.on('request', request => {
    if (request.url().endsWith('/api/process') && request.method() === 'POST') submissions.push(request.url());
  });
  await page.goto('/#app');
  await page.getByLabel('Category name', { exact: true }).fill('E2E invalid short source');
  await page.getByRole('button', { name: '9:16 Shorts · Reels · TikTok', exact: true }).click();
  await page.locator('input[type=file]').setInputFiles(shortSource);
  await expect(page.getByRole('alert')).toContainText('This video is 6s long.');
  await expect(page.getByRole('alert')).toContainText('at least 45s');
  await expect(page.getByRole('button', { name: 'Generate Clips', exact: true })).toBeDisabled();
  expect(submissions).toEqual([]);
  await page.screenshot({ path: path.join(evidence, 'short-source-rejected.png'), fullPage: true });
});

test('invalid media uploads and removal cancellation preserve clip three', async ({ page }) => {
  test.skip(!jobId, 'Requires the real completed generation job.');
  const clipIndex = 2;
  const before = await (await page.request.get(`/api/status/${jobId}`)).json();
  const clipBefore = before.result.clips.find((clip, index) => (clip.index ?? index) === clipIndex);
  expect(clipBefore).toBeTruthy();
  const writes = [];
  page.on('request', request => {
    if (request.url().includes('/api/') && ['POST', 'PUT', 'PATCH', 'DELETE'].includes(request.method())) {
      writes.push({ method: request.method(), url: request.url() });
    }
  });
  await page.goto(`/#edit/${jobId}/${clipIndex}`);
  await expect(page.getByRole('main', { name: 'Video editor' })).toBeVisible();
  await page.locator('#edit-tab-media').click();
  const panel = page.locator('#edit-panel-media');
  await expect(panel.getByRole('button', { name: 'Add images or videos', exact: true })).toBeEnabled();
  const upload = panel.locator('input[type=file]').first();
  await upload.setInputFiles({ name: 'unsupported.txt', mimeType: 'text/plain', buffer: Buffer.from('invalid visual media') });
  await expect(panel.getByRole('alert')).toContainText('unsupported.txt: choose an image');
  expect(writes).toEqual([]);
  const failed = page.waitForResponse(response => response.url().includes(`/api/clip/${jobId}/${clipIndex}/broll/assets`));
  await upload.setInputFiles({ name: 'invalid.png', mimeType: 'image/png', buffer: Buffer.from('these bytes are not a PNG image') });
  const response = await failed;
  expect(response.status()).toBe(400);
  await expect(panel.getByRole('alert')).toContainText(/valid|image|PNG/i);
  await page.screenshot({ path: path.join(evidence, 'invalid-image-rejected.png'), fullPage: true });
  await panel.getByRole('button', { name: 'Done', exact: true }).click();
  if (await page.getByRole('heading', { name: 'Save your changes?', exact: true }).isVisible()) {
    await page.getByRole('button', { name: 'Discard changes', exact: true }).click();
  }
  await page.goto('/#app?tab=history');
  await expect(page.getByRole('region', { name: 'Generated shorts library' })).toBeVisible();
  const title = clipBefore.video_title_for_youtube_short || clipBefore.title;
  const card = page.locator('.local-clip-card').filter({ has: page.getByRole('heading', { name: title, exact: true }) }).first();
  await expect(card).toBeVisible();
  await card.getByRole('button', { name: 'Remove', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'Remove video?', exact: true })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Remove permanently', exact: true })).toBeVisible();
  await page.getByRole('button', { name: 'Cancel', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'Remove video?', exact: true })).not.toBeVisible();
  const after = await (await page.request.get(`/api/status/${jobId}`)).json();
  const clipAfter = after.result.clips.find((clip, index) => (clip.index ?? index) === clipIndex);
  expect(clipAfter).toEqual(clipBefore);
  expect(writes.filter(request => request.method === 'DELETE' || ['PUT', 'PATCH'].includes(request.method))).toEqual([]);
  expect(writes.filter(request => request.method === 'POST')).toHaveLength(1);
  fs.writeFileSync(path.join(evidence, 'validation-observations.json'), JSON.stringify({ clipIndex, writes, rejectedUploadStatus: response.status(), clipUnchanged: true, deletionCancelled: true }, null, 2));
  await page.screenshot({ path: path.join(evidence, 'deletion-cancelled.png'), fullPage: true });
});
