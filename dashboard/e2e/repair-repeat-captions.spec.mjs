import { test, expect } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';
import { createHash } from 'node:crypto';

const repeat = path.resolve('../artifacts/e2e-2026-11-13/repeat-shorter');
const proof = path.join(repeat, 'repair-ui');

test('restore real Mozi captions on both completed shorter-repeat clips', async ({ page, request }, info) => {
    test.setTimeout(30 * 60_000);
    test.skip(process.env.OPENSHORTS_REAL_E2E !== '1', 'Opt in to repair the real required-source repeat clips through the UI.');
    page.setDefaultTimeout(30_000);
    fs.mkdirSync(proof, { recursive: true });
    const jobId = JSON.parse(fs.readFileSync(path.join(repeat, 'job.json'), 'utf8')).jobId;
    const original = JSON.parse(fs.readFileSync(path.join(repeat, 'generation-result.json'), 'utf8'));
    expect(original.status).toBe('completed');
    const clips = original.result?.clips || original.clips;
    expect(clips).toHaveLength(2);
    const results = [];
    const events = [];
    const captures = [];
    page.on('pageerror', error => events.push({ type: 'pageerror', message: error.message }));
    page.on('console', message => { if (message.type() === 'error') events.push({ type: 'console', message: message.text() }); });
    page.on('response', response => {
        if (response.url().includes('/api/') && (response.request().method() !== 'GET' || response.status() >= 400)) {
            captures.push((async () => events.push({ type: 'api', method: response.request().method(),
                url: response.url(), status: response.status(), body: await response.json().catch(() => null) }))());
        }
    });
    const get = async url => {
        const response = await request.get(url, { timeout: 120_000 });
        expect(response.ok(), url).toBeTruthy();
        return response.json();
    };
    const fromStatus = (status, index) => status.result.clips.find(clip => clip.index === index) || status.result.clips[index];
    try {
        for (const [position, originalClip] of clips.entries()) {
            const index = originalClip.index ?? position;
            await test.step(`Repair source clip ${index + 1}`, async () => {
                const before = fromStatus(await get(`/api/status/${jobId}`), index);
                const wordsBefore = await get(`/api/clip/${jobId}/${index}/transcript`);
                expect(wordsBefore.captions.length).toBeGreaterThan(0);
                await page.goto(`/#edit/${jobId}/${index}`);
                const editor = page.getByRole('main', { name: 'Video editor' });
                await expect(editor).toBeVisible();
                await expect.poll(async () => Number(await page.getByLabel('Trim end in seconds').inputValue())).toBeGreaterThan(0);
                await expect(page.getByRole('button', { name: 'Play preview', exact: true }).first()).toBeEnabled();
                await page.locator('#edit-tab-captions').click();
                let panel = page.locator('#edit-panel-captions');
                await expect(panel).toBeVisible();
                await expect(panel.getByRole('button', { name: /^(Apply to this clip|Remove captions)$/ })).toBeEnabled();
                await panel.locator('label.caption-style-choice:has(input[value="cc_mozi"])').click();
                await expect(panel.locator('input[value="cc_mozi"]')).toBeChecked();
                await panel.getByRole('radio', { name: 'bottom', exact: true }).click();
                await panel.getByLabel('Caption font', { exact: true }).selectOption('Arial');
                await expect(panel.getByLabel('One word at a time', { exact: true })).not.toBeChecked();
                const pending = page.waitForResponse(response => new URL(response.url()).pathname === '/api/subtitle'
                    && response.request().method() === 'POST', { timeout: 15 * 60_000 });
                await panel.getByRole('button', { name: 'Apply to this clip', exact: true }).click();
                const response = await pending;
                const mutation = await response.json();
                expect(response.ok(), JSON.stringify(mutation)).toBeTruthy();
                const submitted = response.request().postDataJSON();
                expect(submitted.words == null, 'Use the real stored recognition, without retyped/mock captions.').toBeTruthy();
                expect(submitted).toMatchObject({ job_id: jobId, clip_index: index, preset: 'cc_mozi', effect: 'clipcutter:mozi', font_name: 'Arial' });
                expect(mutation.new_video_url).toContain('/subtitled_');
                await expect(page.locator('.unified-editor-page-state')).toHaveText('Ready to edit', { timeout: 120_000 });
                await page.reload();
                await expect(editor).toBeVisible();
                await expect.poll(async () => Number(await page.getByLabel('Trim end in seconds').inputValue())).toBeGreaterThan(0);
                await expect(page.getByRole('button', { name: 'Play preview', exact: true }).first()).toBeEnabled();
                const media = page.locator('video[aria-label="AI edit preview"]');
                await expect.poll(() => media.evaluate(video => video.readyState)).toBeGreaterThanOrEqual(1);
                const actual = await media.evaluate(video => ({ source: video.currentSrc, duration: video.duration, width: video.videoWidth, height: video.videoHeight }));
                expect(actual.width / actual.height).toBeCloseTo(9 / 16, 5);
                const after = fromStatus(await get(`/api/status/${jobId}`), index);
                const wordsAfter = await get(`/api/clip/${jobId}/${index}/transcript`);
                expect(after.video_url).toBe(mutation.new_video_url);
                expect(after.caption_preset).toBe('cc_mozi');
                expect(after.caption_style).toMatchObject({ effect: 'clipcutter:mozi', font_name: 'Arial', position: 'bottom' });
                expect(after.caption_generation?.status).not.toBe('failed');
                expect(wordsAfter.captions).toEqual(wordsBefore.captions);
                expect(Math.abs(after.duration_seconds - actual.duration)).toBeLessThan(.05);
                await page.locator('#edit-tab-captions').click();
                panel = page.locator('#edit-panel-captions');
                await expect(panel.locator('input[value="cc_mozi"]')).toBeChecked();
                await expect(panel.getByLabel('Caption font', { exact: true })).toHaveValue('Arial');
                await panel.getByLabel('Seek preview').fill('1');
                await expect(panel.locator('[data-caption-effect="mozi"]')).toBeVisible();
                await page.screenshot({ path: path.join(proof, `clip-${index + 1}-repaired-reopened.png`), fullPage: true });
                const videoResponse = await request.get(after.video_url, { timeout: 120_000 });
                expect(videoResponse.ok()).toBeTruthy();
                const bytes = await videoResponse.body();
                expect(bytes.length).toBeGreaterThan(1024);
                const artifact = path.join(proof, `clip-${index + 1}-repaired.mp4`);
                fs.writeFileSync(artifact, bytes);
                results.push({ jobId, index, original_generation_clip: originalClip, before, after,
                    submitted, mutation, words_before: wordsBefore, words_after: wordsAfter, actual,
                    downloaded_actual_video: artifact, sha256: createHash('sha256').update(bytes).digest('hex') });
                fs.writeFileSync(path.join(proof, 'results.json'), JSON.stringify(results, null, 2));
            });
        }
    } finally {
        await Promise.allSettled(captures);
        fs.writeFileSync(path.join(proof, 'browser-events.json'), JSON.stringify(events, null, 2));
        await info.attach('browser-events', { body: JSON.stringify(events, null, 2), contentType: 'application/json' });
        await info.attach('current-ui', { body: await page.locator('body').ariaSnapshot(), contentType: 'text/plain' });
    }
});
