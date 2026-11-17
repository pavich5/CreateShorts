import { test, expect } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';

const evidence = path.resolve('../artifacts/e2e-2026-11-13');
const jobFile = path.join(evidence, 'job.json');
const jobId = process.env.OPENSHORTS_E2E_JOB || (fs.existsSync(jobFile) ? JSON.parse(fs.readFileSync(jobFile)).jobId : null);
test.beforeEach(async ({ page }) => {
    test.setTimeout(180_000);
    page.setDefaultTimeout(30_000);
    test.skip(process.env.OPENSHORTS_REAL_E2E !== '1' || !jobId, 'Requires the mandatory-source generation job.');
    await page.goto(`/#edit/${jobId}/2`);
    await expect(page.getByRole('main', { name: 'Video editor' })).toBeVisible();
    await expect(page.getByLabel('Trim end in seconds')).toHaveValue(/\d/);
});

async function tool(page, name) {
    await page.locator(`#edit-tab-${name}`).click();
    const panel = page.locator(`#edit-panel-${name}`);
    await expect(panel).toBeVisible();
    return panel;
}
async function preset(panel, id) {
    await panel.locator(`label.caption-style-choice:has(input[value="${id}"])`).click();
    await expect(panel.locator(`input[value="${id}"]`)).toBeChecked();
}
async function toggle(control) {
    const checked = await control.isChecked();
    await control.locator('..').click();
    if (checked) await expect(control).not.toBeChecked();
    else await expect(control).toBeChecked();
}

async function loadedPicture(panel) {
    const picture = panel.locator('.remotion-preview-picture');
    await expect.poll(async () => picture.evaluate(element => ({
        videos: [...element.querySelectorAll('video')].map(video => ({ ready: video.readyState, time: video.currentTime })),
        canvases: element.querySelectorAll('canvas').length,
    })).then(state => state.videos.length ? state.videos.every(video => video.ready >= 2 && Math.abs(video.time - 1) < .2) : state.canvases > 0), { timeout: 30_000 }).toBeTruthy();
    await expect.poll(async () => {
        const screenshot = await picture.screenshot({ timeout: 5000 });
        return panel.page().evaluate(async data => {
            const image = new Image(); image.src = `data:image/png;base64,${data}`;
            await image.decode();
            const canvas = document.createElement('canvas'); canvas.width = image.width; canvas.height = image.height;
            const context = canvas.getContext('2d'); context.drawImage(image, 0, 0);
            const pixels = context.getImageData(Math.round(image.width * .45), Math.round(image.height * .33), Math.round(image.width * .1), Math.round(image.height * .06)).data;
            let sum = 0; for (let i = 0; i < pixels.length; i += 4) sum += pixels[i] + pixels[i + 1] + pixels[i + 2];
            return sum / (pixels.length / 4) > 10;
        }, screenshot.toString('base64'));
    }, { timeout: 30_000 }).toBeTruthy();
    return picture;
}

test('all caption presets and applicable styling controls respond without saving', async ({ page }) => {
    const writes = [];
    page.on('request', request => { if (request.url().includes('/api/') && ['POST', 'PUT', 'DELETE'].includes(request.method())) writes.push(request.url()); });
    const panel = await tool(page, 'captions');
    await expect(panel.getByRole('button', { name: /^(Apply to this clip|Remove captions)$/ })).toBeEnabled();
    const presets = ['cc_none', 'cc_marker_sweep', 'cc_mozi', 'cc_negative_keywords', 'hormozi', 'pill', 'lime', 'oneword'];
    const observations = [];
    for (const id of presets) {
        await preset(panel, id);
        await panel.getByLabel('Seek preview').fill('1');
        if (id === 'cc_none') await expect(panel.locator('[data-caption-engine="clipcutter"]')).toHaveCount(0);
        if (['cc_marker_sweep', 'cc_mozi', 'cc_negative_keywords'].includes(id)) {
            await expect(panel.locator(`[data-caption-effect="${id.slice(3)}"]`)).toBeVisible();
            await expect(panel.getByLabel('Dim inactive words')).toBeDisabled();
            await expect(panel.getByLabel('Reveal word by word')).toBeDisabled();
            await expect(panel.getByText('This caption style uses its own word emphasis.', { exact: false })).toBeVisible();
        }
        if (['cc_marker_sweep', 'cc_mozi', 'cc_negative_keywords', 'hormozi'].includes(id)) {
            await page.evaluate(() => document.fonts.ready);
            await (await loadedPicture(panel)).screenshot({ path: path.join(evidence, `controls-caption-${id}-1s.png`) });
        }
        observations.push({ preset: id, checked: true });
    }
    await preset(panel, 'hormozi');
    await expect(panel.getByLabel('Dim inactive words')).toBeEnabled();
    await panel.getByLabel('Dim inactive words').fill('45');
    await expect(panel.getByLabel('Dim inactive words')).toHaveValue('45');
    await toggle(panel.getByLabel('Uppercase captions', { exact: true }));
    await toggle(panel.getByLabel('One word at a time', { exact: true }));
    await toggle(panel.getByLabel('One word at a time', { exact: true }));
    await toggle(panel.getByLabel('Reveal word by word', { exact: true }));
    for (const position of ['top', 'middle', 'bottom']) {
        const option = panel.getByRole('radio', { name: position, exact: true });
        await option.click(); await expect(option).toHaveAttribute('aria-checked', 'true');
    }
    for (const size of ['S', 'M', 'L', 'XL']) {
        const option = panel.getByRole('radio', { name: size, exact: true });
        await option.click(); await expect(option).toHaveAttribute('aria-checked', 'true');
    }
    for (const font of ['Anton', 'Montserrat ExtraBold', 'Verdana', 'Arial', 'Impact', 'Helvetica', 'Georgia', 'Courier New']) {
        await panel.getByLabel('Caption font', { exact: true }).selectOption(font);
        await expect(panel.getByLabel('Caption font', { exact: true })).toHaveValue(font);
    }
    for (const animation of ['Pop', 'Glow', 'Box', 'None']) {
        const option = panel.getByRole('radio', { name: animation, exact: true });
        await option.click(); await expect(option).toHaveAttribute('aria-checked', 'true');
    }
    for (const color of ['White', 'Yellow', 'Cyan', 'Green', 'Red', 'Pink']) {
        const section = panel.getByText('Text color', { exact: true }).locator('..');
        const swatch = section.getByRole('button', { name: color, exact: true });
        await swatch.click(); await expect(swatch).toHaveAttribute('aria-pressed', 'true');
    }
    for (const color of ['Yellow', 'Gold', 'Lime', 'Purple', 'Red', 'Green', 'Blue', 'Pink']) {
        const section = panel.getByText('Highlight', { exact: true }).locator('..');
        const swatch = section.getByRole('button', { name: color, exact: true });
        await swatch.click(); await expect(swatch).toHaveAttribute('aria-pressed', 'true');
    }
    await panel.getByLabel('Caption text color', { exact: true }).fill('#123456');
    await expect(panel.getByLabel('Caption text color', { exact: true })).toHaveValue('#123456');
    await panel.getByLabel('Caption border color', { exact: true }).fill('#654321');
    await panel.getByLabel('Caption border width', { exact: true }).fill('3');
    await expect(panel.getByLabel('Caption border width', { exact: true })).toHaveValue('3');
    await toggle(panel.getByLabel('Caption background', { exact: true }));
    await panel.getByLabel('Caption background color', { exact: true }).fill('#112233');
    await panel.getByLabel('Caption background opacity', { exact: true }).fill('65');
    await expect(panel.getByLabel('Caption background opacity', { exact: true })).toHaveValue('65');
    await preset(panel, 'cc_mozi');
    await toggle(panel.getByLabel('Caption background', { exact: true }));
    await panel.getByLabel('Caption background color', { exact: true }).fill('#112233');
    await panel.getByLabel('Caption background opacity', { exact: true }).fill('65');
    await panel.getByLabel('Seek preview').fill('1');
    await expect.poll(() => panel.locator('[data-caption-engine="clipcutter"] > div').evaluate(element => getComputedStyle(element).backgroundColor)).toBe('rgba(17, 34, 51, 0.65)');
    expect(writes).toEqual([]);
    fs.writeFileSync(path.join(evidence, 'controls-caption-observations.json'), JSON.stringify({ presets: observations, counts: { presets: 8, positions: 3, sizes: 4, fonts: 8, animations: 4, textColors: 6, highlightColors: 8, genericCheckboxToggles: 4, customColors: 3, backgroundOpacity: 2 }, writes }, null, 2));
});

test('all hook appearance controls respond without changing the project', async ({ page }) => {
    const writes = [];
    page.on('request', request => { if (request.url().includes('/api/') && ['POST', 'PUT', 'DELETE'].includes(request.method())) writes.push(request.url()); });
    const panel = await tool(page, 'hook');
    for (const style of ['Pills', 'Classic', 'Dark', 'Yellow', 'Red', 'Outline', 'Outline+']) {
        console.log('hook style', style);
        const option = panel.getByTitle(style, { exact: true });
        await option.click(); await expect(option).toHaveAttribute('aria-pressed', 'true');
    }
    for (const font of ['Montserrat', 'Anton', 'Serif']) {
        console.log('hook font', font);
        const option = panel.getByRole('button', { name: font, exact: true });
        await option.click(); await expect(option).toHaveAttribute('aria-pressed', 'true');
    }
    for (const value of ['auto', 'top', 'center', 'bottom', 'Small', 'Medium', 'Large']) {
        console.log('hook placement/size', value);
        const option = panel.getByRole('radio', { name: value, exact: true });
        await option.click(); await expect(option).toHaveAttribute('aria-checked', 'true');
    }
    const range = panel.getByLabel('Hook duration', { exact: true });
    const max = await range.getAttribute('max');
    await range.fill('2.5'); await expect(range).toHaveValue('2.5');
    expect(Number(max)).toBeGreaterThan(15);
    await panel.getByLabel('Hook text', { exact: true }).fill('Control exploration only');
    await panel.getByLabel('Seek preview').fill('1');
    await panel.locator('.remotion-preview-picture').screenshot({ path: path.join(evidence, 'controls-hook-1s.png') });
    expect(writes).toEqual([]);
    fs.writeFileSync(path.join(evidence, 'controls-hook-observations.json'), JSON.stringify({ styles: 7, fonts: 3, positions: 4, sizes: 3, duration: 2.5, durationMax: Number(max), writes }, null, 2));
});

test('saved hook appearance restores after reloading', async ({ page, request }) => {
    test.skip(process.env.OPENSHORTS_HOOK_PERSISTENCE_READY !== '1', 'Run after the root hook mutation stress test finishes.');
    const status = await (await request.get(`/api/status/${jobId}`)).json();
    const clip = status.result.clips.find(item => item.index === 1) || status.result.clips[1];
    const hook = clip.auto_hook;
    await page.goto(`/#edit/${jobId}/1`);
    const panel = await tool(page, 'hook');
    await expect(panel.getByLabel('Hook text', { exact: true })).toHaveValue(hook.text);
    await expect(panel.getByLabel('Hook duration', { exact: true })).toHaveValue(String(hook.duration_seconds));
    await expect(panel.getByRole('radio', { name: hook.position || 'auto', exact: true })).toHaveAttribute('aria-checked', 'true');
    const styleNames = { pill: 'Pills', classic: 'Classic', dark: 'Dark', yellow: 'Yellow', red: 'Red', outline: 'Outline', outline_yellow: 'Outline+' };
    await expect(panel.getByRole('button', { name: styleNames[hook.style], exact: true })).toHaveAttribute('aria-pressed', 'true');
    const sizeNames = { S: 'Small', M: 'Medium', L: 'Large' };
    await expect(panel.getByRole('radio', { name: sizeNames[hook.size], exact: true })).toHaveAttribute('aria-checked', 'true');
    const fontNames = { montserrat: 'Montserrat', anton: 'Anton', serif: 'Serif' };
    const font = hook.font || (hook.style === 'pill' ? 'montserrat' : 'serif');
    await expect(panel.getByRole('button', { name: fontNames[font], exact: true })).toHaveAttribute('aria-pressed', 'true');
    if (Number.isFinite(hook.y)) {
        await panel.getByLabel('Seek preview').fill('1');
        await expect(panel.locator(`[data-hook-y="${hook.y}"]`)).toHaveCSS('top', `${hook.y * 1920}px`);
    }
});

test('No caption does not allow hidden caption appearance changes', async ({ page }) => {
    const panel = await tool(page, 'captions');
    await preset(panel, 'cc_none');
    await panel.getByLabel('Seek preview').fill('1');
    const before = await panel.locator('.remotion-preview-picture').innerText();
    const pop = panel.getByRole('radio', { name: 'Pop', exact: true });
    const disabled = await pop.isDisabled();
    if (!disabled) {
        await pop.click();
        await panel.getByLabel('Seek preview').fill('1');
    }
    const after = await panel.locator('.remotion-preview-picture').innerText();
    fs.writeFileSync(path.join(evidence, 'controls-no-caption-edge.json'), JSON.stringify({ before, after, popDisabled: disabled,
        noCaptionChecked: await panel.locator('input[value="cc_none"]').isChecked(), removeButton: await panel.getByRole('button', { name: 'Remove captions', exact: true }).count() }, null, 2));
    await page.screenshot({ path: path.join(evidence, 'controls-no-caption-edge.png'), fullPage: true });
    await expect(pop).toBeDisabled();
    expect(after).toBe(before);
});
