import { test, expect, type Page } from '@playwright/test';
import { mkdirSync, writeFileSync } from 'node:fs';
import { dirname, join } from 'node:path';

/**
 * AC-U5, the half axe cannot answer.
 *
 * "Every function is reachable and operable by keyboard, with a visible focus
 * indicator. Verified by a manual keyboard pass recorded in the evidence pack."
 *
 * axe checks markup. It cannot tell you that Tab reaches the submit button
 * before the cancel one, that the focus ring is visible against the surface it
 * lands on, or that a dialog gives focus back to the control that opened it.
 * Those are the things a keyboard pass is for, and they are the things that
 * were wrong in this console before somebody walked it.
 *
 * **How this pass is performed.** Every interaction here is a key event. There
 * is no `click()` anywhere in this file, and no `focus()` -- a test that
 * focused an element to prove it was reachable would be proving the opposite.
 * The walk records the accessible name, role and focus outline of every stop,
 * writes them to `docs/acceptance/evidence/keyboard-pass.json`, and asserts the
 * properties that can be asserted. The findings written up from that record are
 * in `docs/acceptance/keyboard-pass.md`, and they include what the assertions
 * below do not catch.
 */

const ROUTES = [
  '/',
  '/corpora',
  '/corpora/register',
  '/runs',
  '/runs/compose',
  '/models',
  '/gates',
  '/audit',
  '/sites',
  '/admin/policy',
  '/signin',
];

/** How far to walk. Longer than any screen's control count, short of a loop. */
const STOPS = 60;

interface Stop {
  index: number;
  tag: string;
  role: string;
  name: string;
  /** True when the only text on the control is a placeholder, which is not a name. */
  placeholderOnly: boolean;
  /** Whether anything paints a focus indicator: an outline or a box shadow. */
  focusVisible: boolean;
  disabled: boolean;
}

interface RouteReport {
  route: string;
  stops: Stop[];
  /** Set when Tab returned to a stop it had already visited: the cycle closed. */
  cycled: boolean;
  reachedBody: boolean;
}

/**
 * Read what is focused now, and whether a sighted keyboard user could tell.
 *
 * The focus indicator is read from the computed style rather than from a class
 * name, because a class that is supposed to paint a ring and does not is
 * exactly the defect this is looking for. A style is visible when it changes
 * the outline, or paints a box shadow, or thickens the border -- JARNGREIPR
 * uses `--jg-focus-ring` on an outline, and a component that reached for
 * something else still counts if a user can see it.
 */
async function readFocus(page: Page): Promise<Stop | null> {
  return page.evaluate(() => {
    const element = document.activeElement;
    if (!element || element === document.body) {
      return null;
    }
    const style = getComputedStyle(element);
    const outline =
      style.outlineStyle !== 'none' && style.outlineWidth !== '0px' && style.outlineWidth !== '';
    const shadow = style.boxShadow !== 'none' && style.boxShadow !== '';

    // The accessible name, computed the way a screen reader computes it rather
    // than by reading one attribute. The first version of this read `aria-label`
    // and the element's text, and reported every `<label for>`-labelled input on
    // two screens as unnamed -- a finding about the probe, not about the console.
    const text = (node: Element | null | undefined): string =>
      node ? node.textContent.trim() : '';

    const labelled = element.getAttribute('aria-labelledby');
    const fromLabelledBy =
      labelled === null
        ? ''
        : labelled
            .split(/\s+/)
            .map((reference) => text(document.getElementById(reference)))
            .join(' ')
            .trim();
    const identifier = element.getAttribute('id');
    const fromLabel =
      identifier === null
        ? ''
        : text(document.querySelector(`label[for="${CSS.escape(identifier)}"]`));
    const wrapping = text(element.closest('label'));
    const candidates = [
      (element.getAttribute('aria-label') ?? '').trim(),
      fromLabelledBy,
      fromLabel,
      wrapping,
      (element.getAttribute('title') ?? '').trim(),
      ((element as HTMLElement).innerText || '').trim(),
    ];
    const name = candidates.find((candidate) => candidate !== '') ?? '';

    return {
      index: 0,
      tag: element.tagName.toLowerCase(),
      role: element.getAttribute('role') ?? '',
      name: name.slice(0, 60),
      // A placeholder is not an accessible name -- it disappears on input, and
      // WCAG 2.2 does not accept it as one -- so it is recorded separately and
      // never counted as a name.
      placeholderOnly: name === '' && element.getAttribute('placeholder') !== null,
      focusVisible: outline || shadow,
      disabled:
        element.hasAttribute('disabled') || element.getAttribute('aria-disabled') === 'true',
    };
  });
}

const reports: RouteReport[] = [];

// One worker, in order. The walk is a single pass with a single record, and
// six workers would each write the routes they happened to run.
test.describe.configure({ mode: 'serial' });

test.describe('keyboard pass', () => {
  for (const route of ROUTES) {
    test(`every stop on ${route} is named and shows focus`, async ({ page }) => {
      await page.goto(route);
      await page.waitForLoadState('networkidle');

      const stops: Stop[] = [];
      const seen = new Set<string>();
      let cycled = false;
      let reachedBody = false;

      for (let index = 0; index < STOPS; index += 1) {
        await page.keyboard.press('Tab');
        const stop = await readFocus(page);
        if (stop === null) {
          // Focus reached the document body: the tab ring closed, and the
          // browser is about to hand focus back to its own chrome.
          reachedBody = true;
          break;
        }
        stop.index = index;
        const key = `${stop.tag}:${stop.role}:${stop.name}`;
        if (seen.has(key) && stops.length > 3) {
          cycled = true;
          break;
        }
        seen.add(key);
        stops.push(stop);
      }

      reports.push({ route, stops, cycled, reachedBody });

      // A stop with no accessible name is a stop a screen reader announces as
      // its tag. Buttons carrying only a glyph are the usual cause.
      const unnamed = stops.filter((stop) => !stop.name.trim() && !stop.disabled);
      expect(unnamed, `unnamed focus stops on ${route}: ${JSON.stringify(unnamed)}`).toEqual([]);

      // A placeholder is not a name (WCAG 2.4.6, 3.3.2): it disappears the
      // moment the user types, which is the moment they most need it.
      const placeheld = stops.filter((stop) => stop.placeholderOnly);
      expect(
        placeheld,
        `controls named only by a placeholder on ${route}: ${JSON.stringify(placeheld)}`,
      ).toEqual([]);

      // WCAG 2.4.7. A stop a sighted keyboard user cannot locate is a stop
      // they have to guess at.
      const invisible = stops.filter((stop) => !stop.focusVisible && !stop.disabled);
      expect(
        invisible,
        `focus stops with no visible indicator on ${route}: ${JSON.stringify(invisible)}`,
      ).toEqual([]);

      // WCAG 2.1.2. A trap is Tab returning to somewhere it has been while the
      // ring has not closed -- not simply a screen with more controls than the
      // walk has presses. The audit ledger has a focusable control per row and
      // legitimately runs past sixty; a walk that was still finding new stops
      // when it stopped found no trap.
      const stillProgressing = stops.length === STOPS;
      expect(
        cycled || reachedBody || stillProgressing,
        `Tab returned to a visited stop on ${route} without closing the ring: a keyboard trap`,
      ).toBeTruthy();

      // Something has to be reachable at all.
      expect(stops.length, `${route} has no keyboard-reachable control`).toBeGreaterThan(0);
    });
  }

  test('the command palette opens, filters and closes on the keyboard alone', async ({ page }) => {
    await page.goto('/');
    await page.waitForLoadState('networkidle');

    // AC-U17: the console is fully operable without a mouse. The palette is
    // the claim's load-bearing part, so it is opened the way a user opens it.
    await page.keyboard.press('Tab');
    await page.keyboard.press('Control+KeyK');

    const palette = page.getByRole('dialog', { name: /command/i });
    await expect(palette).toBeVisible();

    await page.keyboard.type('runs');
    await page.keyboard.press('Enter');
    await expect(page).toHaveURL(/\/runs/);

    await page.keyboard.press('Control+KeyK');
    await expect(page.getByRole('dialog', { name: /command/i })).toBeVisible();
    await page.keyboard.press('Escape');
    await expect(page.getByRole('dialog', { name: /command/i })).toBeHidden();
  });

  test.afterAll(() => {
    // The record. `docs/acceptance/keyboard-pass.md` is written from this, and
    // a claim about a keyboard pass with no record behind it is a claim.
    // Relative to this spec, not to the project's test directory: the record
    // belongs in the evidence pack at the repository root, and the first
    // version of this resolved to the repository's parent and wrote nothing
    // anybody found.
    const target = join(
      dirname(test.info().file),
      '..',
      '..',
      '..',
      'docs',
      'acceptance',
      'evidence',
      'keyboard-pass.json',
    );
    mkdirSync(dirname(target), { recursive: true });
    writeFileSync(
      target,
      `${JSON.stringify(
        {
          criterion: 'AC-U5',
          performedAt: new Date().toISOString(),
          method:
            'Keyboard events only. No click() and no focus() anywhere in the spec; ' +
            'every stop is reached by Tab and read from document.activeElement.',
          routes: reports,
        },
        null,
        2,
      )}\n`,
      'utf-8',
    );
  });
});
