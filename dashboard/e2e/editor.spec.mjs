import { test, expect } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';

const evidence = path.resolve('../artifacts/e2e-2026-11-13');
const jobFile = path.join(evidence, 'job.json');
const jobId = process.env.OPENSHORTS_E2E_JOB || (fs.existsSync(jobFile) ? JSON.parse(fs.readFileSync(jobFile)).jobId : null);
test.describe.configure({ mode: 'serial' });
test.beforeEach(async ({ page }, info) => {
  test.skip(process.env.OPENSHORTS_REAL_E2E !== '1' || !jobId, 'Requires a real completed generation job.');
  const events = [];
  page.on('pageerror', e => events.push({ type: 'pageerror', message: e.message }));
  page.on('console', m => { if (m.type() === 'error') events.push({ type: 'console', message: m.text() }); });
  page.on('response', r => { if (r.url().includes('/api/') && r.status() >= 400) events.push({ type: 'http', status: r.status(), url: r.url() }); });
  info._events = events;
});
test.afterEach(async ({ page }, info) => {
  await info.attach('browser-events', { body: JSON.stringify(info._events || [], null, 2), contentType: 'application/json' });
  await info.attach('current-ui', { body: await page.locator('body').ariaSnapshot(), contentType: 'text/plain' });
});
async function openEditor(page, index = 0) {
  await page.goto(`/#edit/${jobId}/${index}`);
  await expect(page.getByRole('main', { name: 'Video editor' })).toBeVisible();
  await expect.poll(() => duration(page)).toBeGreaterThan(0);
  await expect(page.getByRole('button', { name: 'Play preview', exact: true }).first()).toBeEnabled();
}
async function apply(page, name, endpoint) {
  await expect(page.getByRole('button', { name, exact: true })).toBeEnabled();
  const pending = page.waitForResponse(r => r.url().includes(endpoint) && ['POST', 'PUT'].includes(r.request().method()), { timeout: 10 * 60_000 });
  await page.getByRole('button', { name, exact: true }).click();
  const response = await pending;
  const body = await response.json();
  expect(response.ok(), JSON.stringify(body)).toBeTruthy();
  await expect(page.getByRole('main', { name: 'Video editor' }).getByText('Saving changes…', { exact: true })).not.toBeVisible({ timeout: 120_000 });
  return body;
}
async function selectTool(page, id) {
  await page.locator(`#edit-tab-${id}`).click();
  await expect(page.locator(`#edit-panel-${id}`)).toBeVisible();
  if (id === 'trim') await expect.poll(() => duration(page)).toBeGreaterThan(0);
  if (id === 'media') await expect(page.getByRole('button', { name: 'Add images or videos', exact: true })).toBeEnabled();
  if (id === 'audio') await expect(page.getByRole('button', { name: 'Upload audio', exact: true })).toBeEnabled();
}
async function duration(page) {
  return Number(await page.getByLabel('Trim end in seconds').inputValue());
}

test('all available editor tabs, playback, seeking and unsaved navigation', async ({ page }) => {
  await openEditor(page);
  const tabs = await page.getByRole('tab').allTextContents();
  fs.writeFileSync(path.join(evidence, 'editor-tools.json'), JSON.stringify(tabs, null, 2));
  await page.getByRole('button', { name: 'Play preview', exact: true }).first().click();
  await expect.poll(() => page.locator('video[aria-label="AI edit preview"]').evaluate(v => v.currentTime)).toBeGreaterThan(.05);
  await page.getByRole('button', { name: 'Pause preview', exact: true }).click();
  await page.getByRole('button', { name: 'Forward one second' }).click();
  await page.getByRole('button', { name: 'Back one second' }).click();
  await page.getByRole('button', { name: 'Mute preview' }).click();
  await page.getByRole('button', { name: 'Unmute preview' }).click();
  await page.getByLabel('Trim start in seconds').fill('0.25');
  await page.locator('#edit-tab-captions').click();
  await expect(page.getByRole('heading', { name: 'Save your changes?' })).toBeVisible();
  await page.getByRole('button', { name: 'Cancel', exact: true }).click();
  await page.locator('#edit-tab-captions').click();
  await page.getByRole('button', { name: 'Discard changes', exact: true }).click();
  for (const id of ['captions', 'hook', 'ai', 'media', 'audio', 'trim']) {
    await selectTool(page, id);
    await expect(page.getByLabel('Seek preview')).toBeEnabled();
    await page.getByLabel('Seek preview').fill('1');
    if (id === 'captions') await expect(page.getByRole('button', { name: 'Apply to this clip', exact: true })).toBeEnabled();
    await page.screenshot({ path: path.join(evidence, `editor-${id}.png`), fullPage: true });
  }
  await expect(page.getByRole('tab')).toHaveCount(6);
});

test('ten consecutive trims agree with loaded media and survive ten reloads', async ({ page }) => {
  await openEditor(page);
  const runs = [];
  for (let i = 0; i < 10; i++) {
    const before = await duration(page);
    const remove = i % 3 === 1;
    if (remove) await page.getByRole('button', { name: 'Remove a section', exact: true }).click();
    await page.getByLabel('Trim start in seconds').fill(remove ? '0.5' : '0.125');
    // Start with a short real excerpt so ten actual full-resolution renders
    // remain practical; subsequent trims exercise already-trimmed footage.
    const end = i === 0 && before > 8 ? 6 : Number((before - .125).toFixed(6));
    await page.getByLabel('Trim end in seconds').fill(remove ? '0.75' : String(end));
    const result = await apply(page, 'Apply trim', '/api/edit/trim');
    const measured = await page.locator('video[aria-label="AI edit preview"]').evaluate(async v => {
      if (v.readyState < 1) await new Promise(resolve => v.addEventListener('loadedmetadata', resolve, { once: true }));
      return v.duration;
    });
    expect(Math.abs(result.duration_seconds - measured)).toBeLessThan(.05);
    expect(measured).toBeLessThan(before);
    await page.reload();
    await expect.poll(() => duration(page)).toBeGreaterThan(0);
    const persisted = await duration(page);
    expect(Math.abs(persisted - measured)).toBeLessThan(.05);
    runs.push({ iteration: i + 1, mode: remove ? 'remove' : 'keep', before, api: result.duration_seconds, measured, persisted, url: result.new_video_url });
    fs.writeFileSync(path.join(evidence, 'browser-trim-iterations.json'), JSON.stringify(runs, null, 2));
  }
  await page.getByLabel('Trim start in seconds').fill('2');
  await page.getByLabel('Trim end in seconds').fill('1');
  await expect(page.getByRole('button', { name: 'Apply trim', exact: true })).toBeDisabled();
  await page.getByRole('button', { name: 'Reset', exact: true }).click();
  await page.screenshot({ path: path.join(evidence, 'trim-ten-times.png'), fullPage: true });
});

test('ten hook replacements persist as one intended hook', async ({ page }) => {
  await openEditor(page, 1);
  if (await duration(page) > 8) {
    await page.getByLabel('Trim end in seconds').fill('8');
    await apply(page, 'Apply trim', '/api/edit/trim');
  }
  await selectTool(page, 'hook');
  const runs = [];
  for (let i = 1; i <= 10; i++) {
    const text = `E2E hook revision ${i}`;
    await page.getByLabel('Hook text', { exact: true }).fill(text);
    const result = await apply(page, 'Update hook', '/api/hook');
    expect(result.burned_hook.text).toBe(text);
    expect(runs.map(run => run.url)).not.toContain(result.new_video_url);
    await page.reload();
    await expect(page.getByRole('main', { name: 'Video editor' })).toBeVisible();
    await selectTool(page, 'hook');
    await expect(page.getByLabel('Hook text', { exact: true })).toHaveValue(text);
    runs.push({ iteration: i, text, url: result.new_video_url });
    fs.writeFileSync(path.join(evidence, 'browser-hook-iterations.json'), JSON.stringify(runs, null, 2));
  }
  await page.screenshot({ path: path.join(evidence, 'hook-ten-times.png'), fullPage: true });
});

test('caption corrections preserve word timing and no-caption setting persists', async ({ page }) => {
  await openEditor(page, 2);
  await selectTool(page, 'captions');
  await expect(page.getByRole('button', { name: 'Apply to this clip', exact: true })).toBeEnabled();
  await page.getByRole('button', { name: /Edit text \(/ }).click();
  const text = page.getByLabel('Caption text', { exact: true });
  const original = await text.inputValue();
  expect(original.trim().length).toBeGreaterThan(0);
  const corrected = original.replace(/\S+/, 'Testing');
  await text.fill(corrected);
  await apply(page, 'Apply to this clip', '/api/subtitle');
  await page.reload();
  await selectTool(page, 'captions');
  await page.getByRole('button', { name: /Edit text \(/ }).click();
  await expect(text).toHaveValue(corrected);
  await page.locator('label.caption-style-choice:has(input[value="cc_none"])').click();
  await apply(page, 'Remove captions', '/api/subtitle');
  await page.reload();
  await selectTool(page, 'captions');
  await expect(page.getByRole('radio', { name: /No caption Keep the picture clear/ })).toBeChecked();
  await page.screenshot({ path: path.join(evidence, 'captions-saved-removed.png'), fullPage: true });
});

test('real AI continuation extends twice and preserves trim', async ({ page }) => {
  await openEditor(page);
  let before = await duration(page);
  const runs = [];
  for (const instructions of ['Add another 10 seconds of relevant content while keeping the existing video intact.', 'Continue the existing narrative for another 5 seconds.']) {
    await selectTool(page, 'ai');
    await page.getByLabel('What would you like to change?', { exact: true }).fill(instructions);
    const result = await apply(page, 'Apply changes', '/api/edit/ai');
    expect(result.duration_seconds).toBeGreaterThan(before + 1);
    runs.push({ instructions, before, result });
    fs.writeFileSync(path.join(evidence, 'browser-ai-continuations.json'), JSON.stringify(runs, null, 2));
    await page.reload();
    await expect.poll(() => duration(page)).toBeGreaterThan(0);
    const after = await duration(page);
    expect(Math.abs(after - result.duration_seconds)).toBeLessThan(.1);
    before = after;
  }
  await page.screenshot({ path: path.join(evidence, 'ai-extended-reopened.png'), fullPage: true });
});

test('multiple source-derived media and audio layers persist through trims and extension', async ({ page }) => {
  await openEditor(page);
  await selectTool(page, 'media');
  const clear = page.getByRole('button', { name: 'Remove all media', exact: true });
  if (await clear.isVisible()) {
    await clear.click();
    await apply(page, 'Save changes', '/broll');
    await expect(page.getByRole('button', { name: 'Add images or videos', exact: true })).toBeEnabled();
  }
  await page.locator('input[type=file]').nth(0).setInputFiles([
    path.join(evidence, 'source-overlay.mp4'), path.join(evidence, 'source-image.jpg'),
  ]);
  const videoRow = page.locator('.broll-editor-placement').filter({ hasText: 'source-overlay.mp4' });
  const imageRow = page.locator('.broll-editor-placement').filter({ hasText: 'source-image.jpg' });
  await expect(videoRow).toBeVisible();
  await videoRow.getByLabel(/start time in seconds/).fill('1');
  await videoRow.getByLabel(/end time in seconds/).fill('4');
  // A second visual can overlap the first without overwriting its ID.
  await imageRow.getByRole('button', { name: /Preview image/ }).click();
  await imageRow.getByLabel(/start time in seconds/).fill('2');
  await imageRow.getByLabel(/end time in seconds/).fill('5');
  await imageRow.getByLabel(/entrance animation/).selectOption('fade');
  await imageRow.getByLabel(/animation duration/).fill('0.4');
  await apply(page, 'Save changes', '/broll');
  await selectTool(page, 'audio');
  await page.locator('input[type=file]').last().setInputFiles(path.join(evidence, 'source-audio.wav'));
  await expect(page.getByLabel('Sound 1 start time in seconds')).toBeVisible();
  await page.getByLabel('Sound 1 start time in seconds').fill('1');
  await page.getByLabel('Sound 1 end time in seconds').fill('4');
  await page.getByLabel('Sound 1 source offset').fill('1');
  await page.getByLabel('Sound 1 volume percent').fill('25');
  await apply(page, 'Save changes', '/broll');
  await page.reload();
  await selectTool(page, 'media');
  await expect(videoRow).toBeVisible();
  await expect(imageRow).toBeVisible();
  await selectTool(page, 'audio');
  await expect(page.getByLabel('Sound 1 volume percent')).toHaveValue('25');
  await selectTool(page, 'trim');
  const before = await duration(page);
  await page.getByLabel('Trim start in seconds').fill('0.25');
  await page.getByLabel('Trim end in seconds').fill(String(before - .25));
  await apply(page, 'Apply trim', '/api/edit/trim');
  await selectTool(page, 'ai');
  await page.getByLabel('What would you like to change?', { exact: true }).fill('Make the video longer by appending another 5 seconds of the original narrative without removing existing content.');
  const extended = await apply(page, 'Apply changes', '/api/edit/ai');
  expect(extended.duration_seconds).toBeGreaterThan(before);
  await page.reload();
  await selectTool(page, 'audio');
  await expect(page.getByLabel('Sound 1 volume percent')).toHaveValue('25');
  await selectTool(page, 'media');
  await expect(videoRow).toBeVisible();
  await page.screenshot({ path: path.join(evidence, 'media-audio-trim-extension.png'), fullPage: true });
  await imageRow.getByRole('button', { name: /Remove image/ }).click();
  await apply(page, 'Save changes', '/broll');
  await selectTool(page, 'audio');
  await page.getByRole('button', { name: 'Remove all audio tracks', exact: true }).click();
  await apply(page, 'Save changes', '/broll');
  await page.reload();
  await selectTool(page, 'audio');
  await expect(page.getByRole('button', { name: 'Remove all audio tracks', exact: true })).toHaveCount(0);
});

test('edited video exports to Clips twice and reloads from the library', async ({ page }) => {
  await page.goto('/#app');
  await page.getByRole('button', { name: 'E2E Alex Hormozi 2026-11-13', exact: true }).click();
  const cards = page.locator('.local-clip-card');
  await expect(cards).toHaveCount(3);
  const original = JSON.parse(fs.readFileSync(path.join(evidence, 'generation-result.json')));
  const title = original.result.clips[0].video_title_for_youtube_short;
  const editedCard = cards.filter({ has: page.getByRole('heading', { name: title, exact: true }) });
  const exports = [];
  for (let i = 1; i <= 2; i++) {
    await editedCard.getByRole('button', { name: /Save to Clips|Save another version/ }).click();
    await page.getByLabel('Video name', { exact: true }).fill(`E2E Alex export ${process.env.OPENSHORTS_E2E_INSTANCE} ${i}`);
    const response = page.waitForResponse(r => r.url().includes('/api/local/library/save') && r.request().method() === 'POST', { timeout: 180_000 });
    await page.getByRole('button', { name: 'Save video', exact: true }).click();
    const result = await response;
    const data = await result.json();
    expect(result.ok(), JSON.stringify(data)).toBeTruthy();
    exports.push({ ...data, expected_source_key: `${jobId}/clip/0` });
    await expect(page.getByRole('heading', { name: 'Save your video', exact: true })).not.toBeVisible();
  }
  fs.writeFileSync(path.join(evidence, 'browser-exports.json'), JSON.stringify(exports, null, 2));
  await page.reload();
  await page.getByRole('button', { name: 'E2E Alex Hormozi 2026-11-13', exact: true }).click();
  await page.screenshot({ path: path.join(evidence, 'exported-library.png'), fullPage: true });
});
