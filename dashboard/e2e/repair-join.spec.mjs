import { test, expect } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';

const evidence = path.resolve('../artifacts/e2e-2026-11-13/final-frozen');
const read = name => JSON.parse(fs.readFileSync(path.join(evidence, name), 'utf8'));
const write = (name, value) => fs.writeFileSync(path.join(evidence, name), JSON.stringify(value, null, 2));
const verifyOnly = process.env.OPENSHORTS_VERIFY_REPAIRED_JOIN === '1';

test(verifyOnly ? 'repaired join reloads with complete boundary word and retained layers' : 'repair the proven padded join through real trim and AI continuation', async ({ page, request }) => {
    test.skip(process.env.OPENSHORTS_REAL_E2E !== '1', 'Uses the real source and configured AI account.');
    test.setTimeout(15 * 60_000);
    const prior = read('final-journey-completed.json');
    const beforeAppend = read('final-journey-before-append.json').clip;
    const jobId = prior.jobId;
    const events = [];
    page.on('pageerror', error => events.push({ type: 'pageerror', message: error.message }));
    async function status() {
        const response = await request.get(`/api/status/${jobId}`);
        expect(response.ok()).toBeTruthy();
        return (await response.json()).result.clips.find(clip => clip.index === 0);
    }
    async function apply(panel, name, endpoint) {
        const button = panel.getByRole('button', { name, exact: true });
        await expect(button).toBeEnabled();
        const pending = page.waitForResponse(response => new URL(response.url()).pathname === endpoint && response.request().method() === 'POST', { timeout: 10 * 60_000 });
        await button.click();
        const response = await pending;
        const body = await response.json();
        events.push({ endpoint, status: response.status(), body });
        expect(response.ok(), JSON.stringify(body)).toBeTruthy();
        await expect(page.locator('.unified-editor-page-state')).toHaveText('Ready to edit', { timeout: 60_000 });
        return body;
    }
    await page.goto('/#app');
    await page.getByRole('button', { name: prior.category, exact: true }).click();
    const card = page.locator('.local-clip-card').filter({ has: page.getByRole('heading', { name: prior.canonicalTitle, exact: true }) });
    await card.getByRole('button', { name: 'Edit', exact: true }).click();
    await expect.poll(async () => Number(await page.getByLabel('Trim end in seconds').inputValue())).toBeGreaterThan(0);
    let trimmed;
    if (verifyOnly) {
        trimmed = read('join-repair-trimmed.json');
    } else {
        await page.getByLabel('Trim start in seconds').fill('0');
        await page.getByLabel('Trim end in seconds').fill(String(beforeAppend.duration_seconds));
        await apply(page.locator('#edit-panel-trim'), 'Apply trim', '/api/edit/trim');
        trimmed = await status();
        write('join-repair-trimmed.json', trimmed);
    }
    const priorLayerIds = prior.final.broll.cues.map(item => item.id).sort();
    expect(trimmed.broll.cues.map(item => item.id).sort()).toEqual(priorLayerIds);
    if (!verifyOnly) {
        await page.locator('#edit-tab-ai').click();
        const panel = page.locator('#edit-panel-ai');
        await panel.getByLabel('What would you like to change?', { exact: true }).fill('Append another 5 seconds of the original narrative after the current ending. Keep all existing captions, hook, images, video overlays and audio intact.');
        await apply(panel, 'Apply changes', '/api/edit/ai');
    }
    const repaired = await status();
    expect(repaired.source_edits.at(-1).source_moment.start).toBeCloseTo(beforeAppend.recipe.canonical_range.end, 3);
    expect(repaired.duration_seconds).toBeGreaterThan(trimmed.duration_seconds + 4);
    expect(repaired.broll.cues.map(item => item.id).sort()).toEqual(priorLayerIds);
    expect(repaired.broll.captions_above_media).toBe(false);
    expect(repaired.caption_style).toEqual(prior.final.caption_style);
    expect(repaired.auto_hook).toEqual(prior.final.auto_hook);
    expect(repaired.caption_words.slice(0, trimmed.caption_words.length - 1)).toEqual(trimmed.caption_words.slice(0, -1));
    const appendAt = repaired.source_edits.at(-1).at;
    const boundaryWord = repaired.caption_words.find(word => word.word.trim().toLowerCase() === 'pursue' && word.start >= appendAt - .01);
    expect(boundaryWord).toBeTruthy();
    expect(boundaryWord.end - boundaryWord.start).toBeGreaterThanOrEqual(.27);
    write('join-repair-applied.json', repaired);
    await page.reload();
    const video = page.locator('video[aria-label="AI edit preview"]');
    await expect.poll(async () => video.evaluate(element => element.duration), { timeout: 60_000 }).toBeCloseTo(repaired.duration_seconds, 1);
    const reopened = await status();
    expect(reopened.video_url).toBe(repaired.video_url);
    await page.getByLabel('Seek preview').fill(String(trimmed.duration_seconds + .2));
    await page.screenshot({ path: path.join(evidence, 'join-repair-reopened.png'), fullPage: true });
    write('join-repair-api-events.json', events);
    write('join-repair-completed.json', { verify_only: verifyOnly, before: prior.final, trimmed, repaired, reopened });
    write('final-journey-completed.json', { ...prior, final: reopened, reloadedPreview: { duration: reopened.duration_seconds }, join_repaired: true });
    expect(events.filter(event => event.type === 'pageerror')).toHaveLength(0);
});
