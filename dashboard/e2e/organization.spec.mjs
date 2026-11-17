import { test, expect } from '@playwright/test';
import path from 'node:path';

test('test-category rename persists, and deletion dialog can be cancelled', async ({ page }) => {
  test.skip(process.env.OPENSHORTS_REAL_E2E !== '1', 'Uses the cancellation test category.');
  await page.goto('/#app');
  const name = 'E2E cancellation 2026-11-13';
  const renamed = name + ' renamed';
  await page.getByRole('button', { name: `Rename campaign ${name}`, exact: true }).click();
  await page.getByLabel('Campaign name', { exact: true }).fill(renamed);
  await page.getByRole('button', { name: 'Save name', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'Rename campaign', exact: true })).not.toBeVisible();
  await page.reload();
  await expect(page.getByRole('button', { name: renamed, exact: true })).toBeVisible();
  await page.getByRole('button', { name: `Delete category ${renamed}`, exact: true }).click();
  await expect(page.getByRole('heading', { name: 'Delete category?', exact: true })).toBeVisible();
  await page.getByRole('button', { name: 'Cancel', exact: true }).click();
  await page.getByRole('button', { name: `Rename campaign ${renamed}`, exact: true }).click();
  await page.getByLabel('Campaign name', { exact: true }).fill(name);
  await page.getByRole('button', { name: 'Save name', exact: true }).click();
  await expect(page.getByRole('button', { name, exact: true })).toBeVisible();
});

test('mobile navigation drawer, settings and collections remain accessible', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto('/#app');
  await page.getByRole('button', { name: 'open navigation', exact: true }).click();
  const drawer = page.getByRole('dialog', { name: 'Navigation', exact: true });
  await expect(drawer).toBeVisible();
  await drawer.getByRole('button', { name: 'All videos', exact: true }).click();
  await expect(drawer).not.toBeVisible();
  await expect(page.getByRole('region', { name: 'Generated shorts library' })).toBeVisible();
  await page.getByRole('navigation', { name: 'Main navigation' }).getByRole('button', { name: 'Settings', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'Workspace settings', exact: true })).toBeVisible();
  await page.screenshot({ path: path.resolve('../artifacts/e2e-2026-11-13/mobile-settings.png'), fullPage: true });
});
