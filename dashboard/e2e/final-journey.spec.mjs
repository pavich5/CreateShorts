import { test, expect } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';

const rootEvidence = path.resolve('../artifacts/e2e-2026-11-13');
const label = process.env.OPENSHORTS_FINAL_GENERATION_LABEL || 'repeat-shorter';
const evidence = path.join(rootEvidence, label);
const category = process.env.OPENSHORTS_FINAL_CATEGORY || 'E2E Alex shorter repeat 2026-11-13';
const read = name => JSON.parse(fs.readFileSync(path.join(evidence, name), 'utf8'));
const write = (name, value) => fs.writeFileSync(path.join(evidence, name), JSON.stringify(value, null, 2));
const clipFrom = status => status.result?.clips?.find(clip => clip.index === 0) || status.result?.clips?.[0] || status.clips?.[0];
const segmentsOf = clip => clip.recipe?.segments || [{ start: clip.start, end: clip.end }];
let jobId;
let canonicalTitle;
let generatedCount;

test.describe.configure({ mode: 'serial' });
test.beforeAll(() => {
    test.skip(process.env.OPENSHORTS_REAL_E2E !== '1', 'Opt in to real video edits and signed-in AI account usage.');
    expect(fs.existsSync(path.join(evidence, 'job.json')), 'Run the real generation test first.').toBeTruthy();
    expect(fs.existsSync(path.join(evidence, 'generation-result.json')), 'Wait for generation completion first.').toBeTruthy();
    jobId = read('job.json').jobId;
    const generated = read('generation-result.json');
    expect(generated.status).toBe('completed');
    const clips = generated.result?.clips || generated.clips;
    generatedCount = clips.length;
    expect(generatedCount).toBeGreaterThan(0);
    canonicalTitle = clipFrom(generated).video_title_for_youtube_short || clipFrom(generated).title;
    expect(canonicalTitle).toBeTruthy();
});
test.beforeEach(async ({ page }, info) => {
    test.setTimeout(45 * 60_000);
    page.setDefaultTimeout(30_000);
    const events = [];
    const captures = [];
    page.on('pageerror', error => events.push({ type: 'pageerror', message: error.message }));
    page.on('console', message => { if (message.type() === 'error') events.push({ type: 'console', message: message.text() }); });
    page.on('response', response => {
        if (!response.url().includes('/api/')) return;
        const method = response.request().method();
        if (['POST', 'PUT', 'DELETE'].includes(method) || response.status() >= 400) {
            captures.push((async () => {
                const body = await response.json().catch(() => null);
                events.push({ type: 'api', method, status: response.status(), url: response.url(), body });
            })());
        }
    });
    info._journey = { events, captures };
});
test.afterEach(async ({ page }, info) => {
    await Promise.allSettled(info._journey?.captures || []);
    const name = info.title.startsWith('continuous') ? 'journey' : 'exports';
    write(`final-${name}-api-events.json`, info._journey?.events || []);
    await info.attach('browser-events', { body: JSON.stringify(info._journey?.events || [], null, 2), contentType: 'application/json' });
    await info.attach('current-ui', { body: await page.locator('body').ariaSnapshot(), contentType: 'text/plain' });
});

async function state(request, name) {
    const response = await request.get(`/api/status/${jobId}`, { timeout: 120_000 });
    expect(response.ok()).toBeTruthy();
    const status = await response.json();
    const clip = clipFrom(status);
    expect(clip).toBeTruthy();
    const snapshot = { status: status.status, clip };
    if (name) write(`final-journey-${name}.json`, snapshot);
    return snapshot;
}
async function placements(request) {
    const response = await request.get(`/api/clip/${jobId}/0/broll`, { timeout: 15 * 60_000 });
    expect(response.ok()).toBeTruthy();
    return response.json();
}
async function transcript(request) {
    const response = await request.get(`/api/clip/${jobId}/0/transcript`, { timeout: 120_000 });
    expect(response.ok()).toBeTruthy();
    return response.json();
}
async function openCategory(page) {
    await page.goto('/#app');
    await page.getByRole('button', { name: category, exact: true }).click();
    await expect(page.getByRole('region', { name: 'Generated shorts library' })).toBeVisible();
    await expect(page.getByRole('heading', { name: category, exact: true })).toBeVisible();
    const card = page.locator('.local-clip-card').filter({ has: page.getByRole('heading', { name: canonicalTitle, exact: true }) });
    await expect(card).toHaveCount(1);
    return card;
}
async function openThroughCard(page) {
    const card = await openCategory(page);
    await card.getByRole('button', { name: 'Edit', exact: true }).click();
    await expect(page.getByRole('main', { name: 'Video editor' })).toBeVisible();
    await expect(page).toHaveTitle(`${canonicalTitle} · OpenShorts Editor`);
    await expect(page.getByLabel('Trim end in seconds')).toHaveValue(/\d/);
    await expect.poll(async () => Number(await page.getByLabel('Trim end in seconds').inputValue())).toBeGreaterThan(0);
    await expect(page.getByRole('button', { name: 'Play preview', exact: true }).first()).toBeEnabled();
    return card;
}
async function tool(page, id) {
    await page.locator(`#edit-tab-${id}`).click();
    const panel = page.locator(`#edit-panel-${id}`);
    await expect(panel).toBeVisible();
    if (id === 'media') await expect(panel.getByRole('button', { name: 'Add images or videos', exact: true })).toBeEnabled();
    if (id === 'audio') await expect(panel.getByRole('button', { name: 'Upload audio', exact: true })).toBeEnabled();
    return panel;
}
async function apply(page, panel, button, endpoint, method = 'POST') {
    await expect(panel.getByRole('button', { name: button, exact: true })).toBeEnabled();
    const responsePromise = page.waitForResponse(response => new URL(response.url()).pathname === endpoint && response.request().method() === method,
        { timeout: 15 * 60_000 });
    await panel.getByRole('button', { name: button, exact: true }).click();
    const response = await responsePromise;
    const body = await response.json();
    expect(response.ok(), JSON.stringify(body)).toBeTruthy();
    await expect(page.locator('.unified-editor-page-state')).toHaveText('Ready to edit', { timeout: 120_000 });
    return body;
}
async function loadedVideo(page, expectedDuration) {
    const video = page.locator('video[aria-label="AI edit preview"]');
    await expect.poll(() => video.evaluate(element => element.readyState), { timeout: 60_000 }).toBeGreaterThanOrEqual(1);
    if (expectedDuration !== undefined) {
        await expect.poll(async () => video.evaluate(element => element.duration), { timeout: 60_000 }).toBeCloseTo(expectedDuration, 1);
    }
    const info = await video.evaluate(element => ({ duration: element.duration, width: element.videoWidth, height: element.videoHeight, source: element.currentSrc }));
    expect(info.width / info.height).toBeCloseTo(9 / 16, 5);
    expect(info.duration).toBeGreaterThan(0);
    return info;
}
async function selectCaptionPreset(panel, value) {
    await panel.locator(`label.caption-style-choice:has(input[value="${value}"])`).click();
    await expect(panel.locator(`input[value="${value}"]`)).toBeChecked();
}

test('continuous real journey preserves trim, layered media, captions, hook and AI append', async ({ page, request }) => {
    await openThroughCard(page);
    // A prior interrupted real run can leave saved test layers. Clear those
    // through the editor before repeating the complete operation sequence.
    const preparation = await state(request);
    if (preparation.clip.broll?.cues?.length) {
        let panel = await tool(page, 'media');
        const clearMedia = panel.getByRole('button', { name: 'Remove all media', exact: true });
        if (await clearMedia.count()) {
            await clearMedia.click();
            await apply(page, panel, 'Save changes', `/api/clip/${jobId}/0/broll`, 'PUT');
        }
        panel = await tool(page, 'audio');
        const clearAudio = panel.getByRole('button', { name: 'Remove all audio tracks', exact: true });
        if (await clearAudio.count()) {
            await clearAudio.click();
            await apply(page, panel, 'Save changes', `/api/clip/${jobId}/0/broll`, 'PUT');
        }
        await tool(page, 'trim');
    }
    const initial = await state(request, 'initial');
    const initialSegments = segmentsOf(initial.clip);
    const initialSourceTail = initial.clip.recipe?.canonical_range?.end ?? initialSegments.at(-1).end;
    const initialPreview = await loadedVideo(page);
    write('final-journey-input.json', { jobId, category, label, canonicalTitle, generatedCount, initialSourceTail, initialPreview });
    await page.getByRole('button', { name: 'Play preview', exact: true }).first().click();
    await expect.poll(() => page.locator('video[aria-label="AI edit preview"]').evaluate(video => video.currentTime)).toBeGreaterThan(.05);
    await page.getByRole('button', { name: 'Pause preview', exact: true }).click();
    await page.getByLabel('Seek preview').fill('1');

    // Keep the actual ending, including a codec tail; the continuation must
    // recover the proven source anchor instead of skipping original speech.
    const fullEnd = await page.getByLabel('Trim end in seconds').inputValue();
    await page.getByLabel('Trim start in seconds').fill('0.125');
    await page.getByLabel('Trim end in seconds').fill(fullEnd);
    await apply(page, page.locator('#edit-panel-trim'), 'Apply trim', '/api/edit/trim');
    const trimmed = await state(request, 'trimmed');
    expect(trimmed.clip.duration_seconds).toBeLessThan(initialPreview.duration);
    expect(segmentsOf(trimmed.clip)[0].start).toBeGreaterThanOrEqual(initialSegments[0].start);
    await page.screenshot({ path: path.join(evidence, 'final-journey-trim.png'), fullPage: true });

    let panel = await tool(page, 'media');
    await panel.locator('input[type=file]').first().setInputFiles([
        path.join(rootEvidence, 'source-overlay.mp4'), path.join(rootEvidence, 'source-image.jpg'),
    ]);
    const videoRow = panel.locator('.broll-editor-placement').filter({ hasText: 'source-overlay.mp4' });
    const imageRow = panel.locator('.broll-editor-placement').filter({ hasText: 'source-image.jpg' });
    await expect(videoRow).toBeVisible();
    await expect(imageRow).toBeVisible();
    await videoRow.getByLabel(/start time in seconds/).fill('1');
    await videoRow.getByLabel(/end time in seconds/).fill('3.5');
    await videoRow.getByRole('button', { name: /Preview video/ }).click();
    await videoRow.getByLabel(/source in seconds/).fill('1');
    await videoRow.getByLabel(/source out seconds/).fill('3.5');
    await videoRow.getByRole('button', { name: 'Preview selected part', exact: true }).click();
    await expect.poll(() => videoRow.locator('video[aria-label^="Uploaded video"]').evaluate(video => video.currentTime)).toBeGreaterThan(1.05);
    await imageRow.getByRole('button', { name: /Preview image/ }).click();
    await imageRow.getByLabel(/start time in seconds/).fill('2');
    await imageRow.getByLabel(/end time in seconds/).fill('5');
    await imageRow.getByLabel(/entrance animation/).selectOption('fade');
    await imageRow.getByLabel(/animation duration/).fill('0.4');
    await panel.getByRole('checkbox', { name: /Captions above added media/ }).uncheck();
    await apply(page, panel, 'Save changes', `/api/clip/${jobId}/0/broll`, 'PUT');
    const withVisuals = await placements(request);
    expect(withVisuals.captions_above_media).toBe(false);
    expect(withVisuals.placements.filter(item => item.kind !== 'audio')).toHaveLength(2);

    panel = await tool(page, 'audio');
    await panel.locator('input[type=file]').last().setInputFiles(path.join(rootEvidence, 'source-audio.wav'));
    await expect(panel.getByLabel('Sound 1 start time in seconds')).toBeVisible();
    await panel.getByLabel('Sound 1 start time in seconds').fill('1');
    await panel.getByLabel('Sound 1 end time in seconds').fill('4');
    await panel.getByLabel('Sound 1 source offset').fill('1');
    await panel.getByLabel('Sound 1 volume percent').fill('25');
    await apply(page, panel, 'Save changes', `/api/clip/${jobId}/0/broll`, 'PUT');
    const withLayers = await placements(request);
    expect(withLayers.placements.filter(item => item.kind === 'audio')).toHaveLength(1);
    const layerIds = withLayers.placements.map(item => item.id).sort();
    write('final-journey-layers-before-append.json', withLayers);

    const originalCaptions = await transcript(request);
    panel = await tool(page, 'captions');
    await selectCaptionPreset(panel, 'cc_mozi');
    await panel.getByLabel('Caption font', { exact: true }).selectOption('Anton');
    const uppercase = panel.getByLabel('Uppercase captions', { exact: true });
    if (await uppercase.isChecked()) await uppercase.locator('..').click();
    const background = panel.getByLabel('Caption background', { exact: true });
    if (!await background.isChecked()) await background.locator('..').click();
    await panel.getByLabel('Caption background color', { exact: true }).fill('#112233');
    await panel.getByLabel('Caption background opacity', { exact: true }).fill('65');
    await panel.getByRole('button', { name: /Edit text \(/ }).click();
    const captionText = panel.getByLabel('Caption text', { exact: true });
    const originalText = await captionText.inputValue();
    const correctedText = originalText.replace(/\S+/, word => word === word.toUpperCase() ? word.toLowerCase() : word.toUpperCase());
    expect(correctedText).not.toBe(originalText);
    expect(correctedText.trim().split(/\s+/)).toHaveLength(originalText.trim().split(/\s+/).length);
    await captionText.fill(correctedText);
    await apply(page, panel, 'Apply to this clip', '/api/subtitle');
    const captioned = await state(request, 'captioned');
    expect(captioned.clip.caption_style).toMatchObject({ effect: 'clipcutter:mozi', font_name: 'Anton', bg_color: '#112233', bg_opacity: .65, uppercase: false });
    const correctedCaptions = await transcript(request);
    expect(correctedCaptions.captions).toHaveLength(originalCaptions.captions.length);
    correctedCaptions.captions.forEach((word, index) => {
        expect(Math.abs(word.startMs - originalCaptions.captions[index].startMs)).toBeLessThanOrEqual(1);
        expect(Math.abs(word.endMs - originalCaptions.captions[index].endMs)).toBeLessThanOrEqual(1);
    });
    expect(correctedCaptions.captions.map(word => word.text).join(' ')).toBe(correctedText);
    write('final-journey-caption-correction.json', { original: originalCaptions, corrected: correctedCaptions, correctedText });
    await page.getByLabel('Seek preview').fill('1');
    await page.screenshot({ path: path.join(evidence, 'final-journey-caption-style.png'), fullPage: true });

    const hookVariants = [
        { text: 'E2E final journey first hook', style: 'Dark', font: 'Anton', position: 'top', size: 'Small', duration: '3.1' },
        { text: 'E2E final journey final hook', style: 'Yellow', font: 'Montserrat', position: 'bottom', size: 'Large', duration: '3.6' },
    ];
    const hooks = [];
    for (const [index, hook] of hookVariants.entries()) {
        panel = await tool(page, 'hook');
        await panel.getByLabel('Hook text', { exact: true }).fill(hook.text);
        await panel.getByTitle(hook.style, { exact: true }).click();
        await panel.getByRole('button', { name: hook.font, exact: true }).click();
        await panel.getByRole('radio', { name: hook.position, exact: true }).click();
        await panel.getByRole('radio', { name: hook.size, exact: true }).click();
        await panel.getByLabel('Hook duration', { exact: true }).fill(hook.duration);
        const hookAction = await panel.getByRole('button', { name: 'Update hook', exact: true }).count() ? 'Update hook' : 'Add hook';
        const result = await apply(page, panel, hookAction, '/api/hook');
        expect(result.burned_hook.text).toBe(hook.text);
        expect(Number.isFinite(result.burned_hook.y)).toBeTruthy();
        expect(Number.isFinite(result.burned_hook.height)).toBeTruthy();
        expect(hooks.map(item => item.new_video_url)).not.toContain(result.new_video_url);
        hooks.push(result);
        if (index === 0) {
            const removed = await apply(page, panel, 'remove hook from clip', '/api/hook');
            expect(removed.burned_hook).toBeNull();
            await expect(panel.getByLabel('Hook text', { exact: true })).toHaveValue('');
            expect((await state(request, 'hook-removed')).clip.auto_hook).toBeFalsy();
        }
    }
    write('final-journey-hook-replacements.json', hooks);
    const beforeAppend = await state(request, 'before-append');
    expect(beforeAppend.clip.auto_hook).toMatchObject({ text: hookVariants[1].text, position: 'bottom', size: 'L', style: 'yellow', font: 'montserrat', duration_seconds: 3.6 });
    expect((await placements(request)).captions_above_media).toBe(false);

    panel = await tool(page, 'ai');
    await panel.getByLabel('What would you like to change?', { exact: true }).fill('Append another 5 seconds of the original narrative after the current ending. Keep all existing video, captions, hook, images, video overlays and audio intact.');
    const extended = await apply(page, panel, 'Apply changes', '/api/edit/ai');
    expect(extended.duration_seconds).toBeGreaterThan(beforeAppend.clip.duration_seconds + 4);
    const afterAppend = await state(request, 'after-append');
    expect(afterAppend.clip.source_edits.at(-1).mode).toBe('append');
    expect(afterAppend.clip.auto_hook).toMatchObject(beforeAppend.clip.auto_hook);
    expect(afterAppend.clip.caption_style).toMatchObject(beforeAppend.clip.caption_style);
    expect(afterAppend.clip.recipe.canonical_range.end).toBeCloseTo(initialSourceTail, 2);
    const keptWords = beforeAppend.clip.caption_words;
    const afterWords = afterAppend.clip.caption_words;
    // A final boundary word can legitimately continue across the source seam.
    expect(afterWords.slice(0, Math.max(0, keptWords.length - 1))).toEqual(keptWords.slice(0, -1));
    const afterLayers = await placements(request);
    expect(afterLayers.placements.map(item => item.id).sort()).toEqual(layerIds);
    expect(afterLayers.captions_above_media).toBe(false);
    write('final-journey-layers-after-append.json', afterLayers);
    await page.screenshot({ path: path.join(evidence, 'final-journey-ai-appended.png'), fullPage: true });
    await panel.getByRole('button', { name: 'Original', exact: true }).click();
    const originalVersion = await loadedVideo(page, beforeAppend.clip.duration_seconds);
    expect(Math.abs(originalVersion.duration - beforeAppend.clip.duration_seconds)).toBeLessThan(.05);
    await expect(panel.getByRole('button', { name: 'Apply changes', exact: true })).toBeDisabled();
    await panel.getByRole('button', { name: 'Edited', exact: true }).click();
    const editedVersion = await loadedVideo(page, afterAppend.clip.duration_seconds);
    expect(Math.abs(editedVersion.duration - afterAppend.clip.duration_seconds)).toBeLessThan(.05);
    write('final-journey-preview-versions.json', { originalVersion, editedVersion });

    await page.reload();
    await expect(page.getByRole('main', { name: 'Video editor' })).toBeVisible();
    const reloadedPreview = await loadedVideo(page, afterAppend.clip.duration_seconds);
    expect(Math.abs(reloadedPreview.duration - afterAppend.clip.duration_seconds)).toBeLessThan(.05);
    panel = await tool(page, 'captions');
    await expect(panel.locator('input[value="cc_mozi"]')).toBeChecked();
    await expect(panel.getByLabel('Caption font', { exact: true })).toHaveValue('Anton');
    await expect(panel.getByLabel('Caption background opacity', { exact: true })).toHaveValue('65');
    panel = await tool(page, 'hook');
    await expect(panel.getByLabel('Hook text', { exact: true })).toHaveValue(hookVariants[1].text);
    await expect(panel.getByTitle('Yellow', { exact: true })).toHaveAttribute('aria-pressed', 'true');
    await expect(panel.getByRole('radio', { name: 'Large', exact: true })).toHaveAttribute('aria-checked', 'true');
    await expect(panel.getByLabel('Hook duration', { exact: true })).toHaveValue('3.6');
    panel = await tool(page, 'audio');
    await expect(panel.getByLabel('Sound 1 volume percent')).toHaveValue('25');
    panel = await tool(page, 'media');
    await expect(panel.locator('.broll-editor-placement').filter({ hasText: 'source-overlay.mp4' })).toBeVisible();
    await expect(panel.locator('.broll-editor-placement').filter({ hasText: 'source-image.jpg' })).toBeVisible();
    await expect(panel.getByRole('checkbox', { name: /Captions above added media/ })).not.toBeChecked();
    await page.getByLabel('Seek preview').fill('2.5');
    await page.screenshot({ path: path.join(evidence, 'final-journey-reopened-layering.png'), fullPage: true });
    await panel.getByRole('button', { name: 'Done', exact: true }).click();
    await expect(page.getByRole('main', { name: 'Video editor' })).not.toBeVisible();
    await openThroughCard(page);
    const reopened = await state(request, 'reopened');
    expect(reopened.clip.video_url).toBe(afterAppend.clip.video_url);
    expect(reopened.clip.recipe).toEqual(afterAppend.clip.recipe);
    write('final-journey-completed.json', { jobId, category, label, canonicalTitle, generatedCount, initialPreview, reloadedPreview,
        expected_source_key: `${jobId}/clip/0`, final: reopened.clip });
});

test('final edited timeline saves two real export versions through the UI', async ({ page, request }) => {
    expect(fs.existsSync(path.join(evidence, 'final-journey-completed.json'))).toBeTruthy();
    const final = read('final-journey-completed.json');
    const card = await openCategory(page);
    const exports = [];
    for (const name of [1, 2].map(index => `E2E final journey ${process.env.OPENSHORTS_E2E_INSTANCE} ${index}`)) {
        await card.getByRole('button', { name: /Save to Clips|Save another version/ }).click();
        await page.getByLabel('Video name', { exact: true }).fill(name);
        const pending = page.waitForResponse(response => new URL(response.url()).pathname === '/api/local/library/save' && response.request().method() === 'POST', { timeout: 180_000 });
        await page.getByRole('button', { name: 'Save video', exact: true }).click();
        const response = await pending;
        const result = await response.json();
        expect(response.ok(), JSON.stringify(result)).toBeTruthy();
        exports.push({ ...result, name, job_id: jobId, clip_index: 0, expected_source_key: `${jobId}/clip/0`,
            expected_duration: final.final.duration_seconds, video_url: final.final.video_url });
        write('browser-exports.json', exports);
        await expect(page.getByRole('heading', { name: 'Save your video', exact: true })).not.toBeVisible();
    }
    expect(exports[0].saved_path).not.toBe(exports[1].saved_path);
    const persisted = await state(request, 'exported');
    expect(persisted.clip.video_url).toBe(final.final.video_url);
    await page.reload();
    await page.getByRole('button', { name: category, exact: true }).click();
    await page.screenshot({ path: path.join(evidence, 'final-journey-two-exports.png'), fullPage: true });
});
