import { test, expect } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';

const evidence = path.resolve('../artifacts/e2e-2026-11-13');
const jobFile = path.join(evidence, 'job.json');
const jobId = process.env.OPENSHORTS_E2E_JOB || (fs.existsSync(jobFile) ? JSON.parse(fs.readFileSync(jobFile)).jobId : null);
const clipIndex = 1;
let nativeEscapeCapability;

test.beforeEach(async ({ page }, info) => {
  test.skip(process.env.OPENSHORTS_REAL_E2E !== '1' || !jobId, 'Requires the real completed generation job.');
  test.setTimeout(180_000);
  info.previewWrites = [];
  info.previewErrors = [];
  page.on('request', request => {
    if (request.url().includes('/api/') && ['POST', 'PUT', 'PATCH', 'DELETE'].includes(request.method())) {
      info.previewWrites.push({ method: request.method(), url: request.url() });
    }
  });
  page.on('pageerror', error => info.previewErrors.push(error.message));
  const status = await (await page.request.get(`/api/status/${jobId}`)).json();
  info.previewClipBefore = status.result.clips.find((clip, index) => (clip.index ?? index) === clipIndex);
  expect(info.previewClipBefore).toBeTruthy();
  await page.goto(`/#edit/${jobId}/${clipIndex}`);
  await expect(page.getByRole('main', { name: 'Video editor' })).toBeVisible();
  await expect(page.getByLabel('Trim end in seconds')).toHaveValue(/\d/);
});

test.afterEach(async ({ page }, info) => {
  if (!info.previewClipBefore) return;
  const status = await (await page.request.get(`/api/status/${jobId}`)).json();
  const after = status.result.clips.find((clip, index) => (clip.index ?? index) === clipIndex);
  const observations = { clipIndex, writes: info.previewWrites, pageErrors: info.previewErrors,
    clipUnchanged: JSON.stringify(after) === JSON.stringify(info.previewClipBefore) };
  await info.attach('read-only-preview-observations', { body: JSON.stringify(observations, null, 2), contentType: 'application/json' });
  fs.writeFileSync(info.outputPath('read-only-preview-observations.json'), JSON.stringify(observations, null, 2));
  expect(info.previewWrites).toEqual([]);
  expect(info.previewErrors).toEqual([]);
  expect(after).toEqual(info.previewClipBefore);
});

async function nativePreview(page) {
  const panel = page.locator('#edit-panel-trim');
  const video = panel.locator('video[aria-label="AI edit preview"]');
  await expect.poll(() => video.evaluate(element => element.readyState)).toBeGreaterThanOrEqual(2);
  await expect.poll(() => video.evaluate(element => element.duration)).toBeGreaterThan(0);
  expect(await video.evaluate(element => element.error)).toBeNull();
  const toolbar = panel.locator('.clip-preview-toolbar');
  return { panel, video, toolbar, slider: panel.getByLabel('Seek preview') };
}

async function seek(slider, video, value) {
  await slider.fill(String(value));
  await expect.poll(() => video.evaluate(element => element.currentTime)).toBeCloseTo(value, 1);
  await expect.poll(() => video.evaluate(element => element.seeking)).toBe(false);
}

async function exitsAfterEscape(page) {
  await page.keyboard.press('Escape');
  try {
    await expect.poll(() => page.evaluate(() => document.fullscreenElement === null), { timeout: 2000 }).toBe(true);
    return true;
  } catch {
    return false;
  }
}

async function nativeEscapeBaseline(page) {
  if (nativeEscapeCapability !== undefined) return nativeEscapeCapability;
  const baseline = await page.context().newPage();
  try {
    await baseline.route('**/__playwright-fullscreen-baseline', route => route.fulfill({ contentType: 'text/html',
      body: `<section id="native-preview"><button onclick="document.querySelector('#native-preview').requestFullscreen()">Enter fullscreen</button></section>` }));
    await baseline.goto(new URL('/__playwright-fullscreen-baseline', page.url()).href);
    await baseline.getByRole('button', { name: 'Enter fullscreen', exact: true }).click();
    await expect.poll(() => baseline.evaluate(() => document.fullscreenElement?.id)).toBe('native-preview');
    nativeEscapeCapability = await exitsAfterEscape(baseline);
    if (!nativeEscapeCapability) await baseline.evaluate(() => document.exitFullscreen());
    return nativeEscapeCapability;
  } finally {
    await baseline.close();
  }
}

test('native preview seeks at boundaries, steps, mutes, pauses, ends and replays', async ({ page }, info) => {
  const { panel, video, toolbar, slider } = await nativePreview(page);
  const duration = await video.evaluate(element => element.duration);
  const dimensions = await video.evaluate(element => ({ width: element.videoWidth, height: element.videoHeight }));
  expect(dimensions.width * 16).toBe(dimensions.height * 9);
  await seek(slider, video, 0);
  await toolbar.getByRole('button', { name: 'Back one second', exact: true }).click();
  await expect.poll(() => video.evaluate(element => element.currentTime)).toBe(0);
  await toolbar.getByRole('button', { name: 'Forward one second', exact: true }).click();
  await expect.poll(() => video.evaluate(element => element.currentTime)).toBeCloseTo(1, 1);
  await toolbar.getByRole('button', { name: 'Back one second', exact: true }).click();
  await expect.poll(() => video.evaluate(element => element.currentTime)).toBe(0);
  await seek(slider, video, duration);
  await toolbar.getByRole('button', { name: 'Forward one second', exact: true }).click();
  await expect.poll(() => video.evaluate(element => element.currentTime)).toBeCloseTo(duration, 1);
  await seek(slider, video, .25);
  await toolbar.getByRole('button', { name: 'Mute preview', exact: true }).click();
  await expect.poll(() => video.evaluate(element => element.muted)).toBe(true);
  await toolbar.getByRole('button', { name: 'Unmute preview', exact: true }).click();
  await expect.poll(() => video.evaluate(element => element.muted)).toBe(false);
  await toolbar.getByRole('button', { name: 'Play preview', exact: true }).click();
  await expect.poll(() => video.evaluate(element => element.currentTime)).toBeGreaterThan(.5);
  await toolbar.getByRole('button', { name: 'Pause preview', exact: true }).click();
  await expect.poll(() => video.evaluate(element => element.paused)).toBe(true);
  const pausedAt = await video.evaluate(element => element.currentTime);
  await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
  expect(await video.evaluate(element => element.currentTime)).toBeCloseTo(pausedAt, 2);
  await seek(slider, video, Number((duration - .35).toFixed(2)));
  await toolbar.getByRole('button', { name: 'Play preview', exact: true }).click();
  await expect.poll(() => video.evaluate(element => element.ended), { timeout: 15_000 }).toBe(true);
  await expect(toolbar.getByRole('button', { name: 'Play preview', exact: true })).toBeEnabled();
  await toolbar.getByRole('button', { name: 'Play preview', exact: true }).click();
  await expect.poll(() => video.evaluate(element => element.currentTime)).toBeLessThan(duration - 1);
  await toolbar.getByRole('button', { name: 'Pause preview', exact: true }).click();
  expect(await video.evaluate(element => element.error)).toBeNull();
  fs.writeFileSync(info.outputPath('native-preview-controls.json'), JSON.stringify({ duration, dimensions, pausedAt,
    zeroClamped: true, endClamped: true, steps: true, mute: true, unmute: true, naturalEnd: true, replay: true }, null, 2));
  await panel.screenshot({ path: info.outputPath('native-controls.png') });
});

for (const tool of ['trim', 'hook']) {
  test(`${tool} preview enters real fullscreen and checks exit against the native browser`, async ({ page }, info) => {
    if (tool === 'trim') await nativePreview(page);
    else {
      await page.locator('#edit-tab-hook').click();
      await expect(page.locator('#edit-panel-hook')).toBeVisible();
      await expect(page.locator('#edit-panel-hook').getByLabel('Seek preview')).toBeEnabled();
    }
    const baselineEscapeExit = await nativeEscapeBaseline(page);
    const panel = page.locator(`#edit-panel-${tool}`);
    expect(await page.evaluate(() => document.fullscreenElement)).toBeNull();
    await panel.getByRole('button', { name: 'Full screen preview', exact: true }).click();
    await expect.poll(() => page.evaluate(() => document.fullscreenElement?.getAttribute('aria-label'))).toBe('Clip preview and timeline');
    expect(await page.evaluate(selector => document.fullscreenElement === document.querySelector(`${selector} .clip-preview`), `#edit-panel-${tool}`)).toBe(true);
    await page.screenshot({ path: info.outputPath(`${tool}-fullscreen.png`) });
    const exitedWithEscape = await exitsAfterEscape(page);
    if (baselineEscapeExit) expect(exitedWithEscape).toBe(true);
    let cleanupExitFullscreen = false;
    if (!exitedWithEscape) {
      // Headless Chrome may retain native fullscreen after Playwright's
      // injected Escape even on the bare browser baseline. This is cleanup,
      // not evidence that a real user's Escape key has been verified.
      expect(baselineEscapeExit).toBe(false);
      await page.evaluate(() => document.exitFullscreen());
      cleanupExitFullscreen = true;
    }
    await expect.poll(() => page.evaluate(() => document.fullscreenElement === null)).toBe(true);
    await expect(panel).toBeVisible();
    const result = { tool, browser: page.context().browser().version(), enteredNativeFullscreen: true,
      baselineEscapeExit, exitedWithEscape, cleanupExitFullscreen,
      ...(exitedWithEscape ? {} : { limitation: 'Injected Escape also failed on the native browser baseline; real user Escape remains unverified.' }) };
    await info.attach('fullscreen-result', { body: JSON.stringify(result, null, 2), contentType: 'application/json' });
    fs.writeFileSync(info.outputPath('fullscreen-result.json'), JSON.stringify(result, null, 2));
  });
}
