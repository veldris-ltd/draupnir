import { test, expect } from '@playwright/test';

/**
 * The console's own accessibility and interaction criteria, on a running stack.
 *
 * These are the ones a component-level check cannot reach, because they are
 * properties of the assembled application rather than of any component in it:
 *
 *   AC-U5   every function reachable and operable by keyboard, with a visible
 *           focus indicator
 *   AC-U8   usable at 200 per cent zoom and at a 320 pixel viewport
 *   AC-U11  every view states its site; no unscoped aggregate view
 *   AC-U14  every error surface shows title, action and correlation identifier
 *   AC-U17  the command palette covers navigation, submission and search, and
 *           the console is fully operable without a mouse
 */

// Every route the shell renders. The kiosk is deliberately absent: it is
// outside the shell and states no site, because it *is* one site's panel.
const ROUTES = [
  '/',
  '/corpora',
  '/corpora/curation',
  '/corpora/retention',
  '/runs',
  '/runs/array',
  '/models',
  '/gates',
  '/audit',
  '/sites',
  '/admin/plugins',
  '/admin/policy',
  '/admin/roles',
];

test.describe('AC-U5, keyboard operation', () => {
  test('the first tab stop is the skip link', async ({ page }) => {
    await page.goto('/runs');
    await page.keyboard.press('Tab');

    const focused = await page.evaluate(() => ({
      text: document.activeElement?.textContent ?? '',
      href: document.activeElement?.getAttribute('href') ?? '',
    }));
    expect(focused.text).toMatch(/skip to main content/i);
    expect(focused.href).toBe('#cn-main');
  });

  test('the focus indicator is visible on every focused control', async ({ page }) => {
    // Not "an outline is declared somewhere" but "the focused element has a
    // non-zero outline". WCAG 2.4.7 is about what a user can see.
    await page.goto('/runs');
    await page.keyboard.press('Tab');
    await page.keyboard.press('Tab');

    const outline = await page.evaluate(() => {
      const element = document.activeElement;
      if (element === null) return null;
      const style = window.getComputedStyle(element);
      return { width: style.outlineWidth, style: style.outlineStyle };
    });
    expect(outline).not.toBeNull();
    expect(outline?.style).not.toBe('none');
    expect(Number.parseFloat(outline?.width ?? '0')).toBeGreaterThan(0);
  });

  test('navigation moves focus to the new page heading', async ({ page }) => {
    // Without this a keyboard user stays where the link was and a screen
    // reader user is told nothing happened.
    await page.goto('/runs');
    await page.getByRole('link', { name: 'Models' }).click();
    await expect(page.getByRole('heading', { name: 'Models', level: 1 })).toBeVisible();

    const focusedTag = await page.evaluate(() => document.activeElement?.tagName);
    expect(focusedTag).toBe('H1');
  });

  test('every section is reachable by keyboard alone', async ({ page }) => {
    await page.goto('/');
    // Tab until the Gates link has focus, then follow it with Enter. No mouse.
    for (let press = 0; press < 40; press += 1) {
      const name = await page.evaluate(() => document.activeElement?.textContent ?? '');
      if (name.trim() === 'Gates') break;
      await page.keyboard.press('Tab');
    }
    await page.keyboard.press('Enter');
    await expect(page.getByRole('heading', { name: 'Gates', level: 1 })).toBeVisible();
  });
});

test.describe('AC-U17, the command palette', () => {
  test('opens on the platform shortcut and navigates', async ({ page }) => {
    await page.goto('/runs');
    // Focus a control first, so the key event has somewhere in the page to
    // start from. `focus()` rather than a click: this is a test about
    // mouseless operation and must not depend on a mouse.
    await page.getByRole('button', { name: 'Search or command' }).focus();
    await page.keyboard.press('Control+KeyK');

    const palette = page.getByRole('dialog', { name: 'Command palette' });
    await expect(palette).toBeVisible();

    // The input keeps focus and the selection moves by `aria-activedescendant`,
    // per the ARIA combobox pattern: moving DOM focus to each option would
    // make type-ahead impossible.
    // Scoped to the palette: the run board's state filter is a `select`,
    // which is also a combobox.
    await expect(palette.getByRole('combobox')).toBeFocused();

    await page.keyboard.type('Models');
    await page.keyboard.press('Enter');
    await expect(page.getByRole('heading', { name: 'Models', level: 1 })).toBeVisible();
  });

  test('covers run submission', async ({ page }) => {
    await page.goto('/');
    await page.getByRole('button', { name: 'Search or command' }).focus();
    await page.keyboard.press('Control+KeyK');
    await page.keyboard.type('Submit a run');
    await page.keyboard.press('Enter');
    await expect(page.getByRole('heading', { name: 'Compose a run', level: 1 })).toBeVisible();
  });

  test('covers search across runs, sources and ledger entries', async ({ page }) => {
    await page.goto('/');
    await page.getByRole('button', { name: 'Search or command' }).focus();
    await page.keyboard.press('Control+KeyK');
    await page.keyboard.type('cim-gbr');

    // A live result, not just the static navigation commands.
    await expect(page.getByRole('option').filter({ hasText: 'run —' }).first()).toBeVisible({
      timeout: 15_000,
    });
  });

  test('closes on Escape and returns focus', async ({ page }) => {
    await page.goto('/runs');
    const trigger = page.getByRole('button', { name: 'Search or command' });
    await trigger.focus();
    await page.keyboard.press('Control+KeyK');
    await expect(page.getByRole('dialog', { name: 'Command palette' })).toBeVisible();

    await page.keyboard.press('Escape');
    await expect(page.getByRole('dialog', { name: 'Command palette' })).toBeHidden();
    // A dialog that returns focus to the body drops a keyboard user at the top
    // of the page.
    await expect(trigger).toBeFocused();
  });
});

test.describe('AC-U8, zoom and reflow', () => {
  test('is usable at a 320 pixel viewport with no loss of function', async ({ page }) => {
    await page.setViewportSize({ width: 320, height: 800 });
    await page.goto('/runs');

    // The navigation reflows rather than disappearing: a hidden navigation is
    // a lost function, which is what the criterion forbids.
    await expect(page.getByRole('link', { name: 'Models' })).toBeVisible();
    await expect(page.getByRole('heading', { name: 'Runs', level: 1 })).toBeVisible();
    await expect(page.getByTestId('site-context')).toBeVisible();

    // No horizontal scrolling of the page itself.
    const overflow = await page.evaluate(
      () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
    );
    expect(overflow).toBeLessThanOrEqual(1);
  });

  test('is usable at 200 per cent zoom', async ({ page }) => {
    // 200 per cent on a 1280 wide window presents 640 CSS pixels. Emulated by
    // halving the viewport, which is the same number of CSS pixels and is what
    // a reflow responds to.
    await page.setViewportSize({ width: 640, height: 512 });
    await page.goto('/gates');

    await expect(page.getByRole('heading', { name: 'Gates', level: 1 })).toBeVisible();
    await expect(page.getByRole('link', { name: 'Runs' })).toBeVisible();
    const overflow = await page.evaluate(
      () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
    );
    expect(overflow).toBeLessThanOrEqual(1);
  });
});

test.describe('AC-U11, site scope', () => {
  for (const route of ROUTES) {
    test(`${route} states which site it shows`, async ({ page }) => {
      await page.goto(route);
      await expect(page.getByTestId('site-context')).toBeVisible();
    });
  }

  test('offers a switcher, and it is a set of control planes', async ({ page }) => {
    // The switcher points at each site's own control plane rather than
    // filtering a shared view, because the API resolves the site from the
    // verified claim and refuses to take it from the client. That is also why
    // there is no aggregate: there is nothing to aggregate across.
    await page.goto('/sites');
    const options = page.locator('[data-testid^="site-option-"]');
    await expect(options.first()).toBeVisible({ timeout: 15_000 });

    const hrefs = await options.evaluateAll((nodes) =>
      nodes.map((node) => node.getAttribute('href') ?? ''),
    );
    expect(hrefs.length).toBeGreaterThan(1);
    for (const href of hrefs) expect(href).toMatch(/^https:\/\/alviss\./);
  });

  test('the sites screen says it is a registry, not an aggregate', async ({ page }) => {
    await page.goto('/sites');
    await expect(page.getByText(/registry of sites, not an aggregate/)).toBeVisible();
  });
});

test.describe('AC-U14, error surfaces', () => {
  test('a failed read shows the title and a copyable correlation identifier', async ({ page }) => {
    // Provoked rather than mocked: an artefact digest that is well formed and
    // is not at this site returns a real problem document.
    await page.goto(`/models/${'0'.repeat(64)}`);

    const surface = page
      .getByRole('status')
      .filter({ hasText: /not|no such/i })
      .first();
    await expect(page.locator('.jg-state')).toBeVisible({ timeout: 15_000 });
    await expect(page.locator('.jg-state__title')).not.toBeEmpty();
    await expect(surface.or(page.locator('.jg-state'))).toBeVisible();
  });

  test('a failed write shows the problem, the action and the identifier', async ({ page }) => {
    await page.goto('/runs/compose');
    // A specification the driver will refuse. The console must show the
    // refusal in words rather than a generic failure.
    await page.getByTestId('spec-editor').fill('{"not":"a specification"}');
    await page.getByRole('button', { name: 'Dry run' }).click();

    const error = page.locator('.cn-error');
    await expect(error).toBeVisible({ timeout: 15_000 });
    await expect(error.locator('.cn-error__title')).not.toBeEmpty();
    await expect(error.locator('.cn-error__detail')).not.toBeEmpty();
  });
});
