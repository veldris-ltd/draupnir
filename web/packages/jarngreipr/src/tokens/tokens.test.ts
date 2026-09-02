import { describe, expect, it } from 'vitest';
import { readSource } from '../../../../tests/source';

const CSS = readSource('packages/jarngreipr/src/tokens/tokens.css');

/**
 * The ramp, checked rather than asserted in a comment (AC-U7).
 *
 * The contrast figures written above each ramp in `tokens.css` are the kind of
 * documentation that is true on the day it is typed and silently false three
 * colour tweaks later. These tests recompute them from the file, so a ramp
 * change that breaks WCAG 2.2 AA fails the build instead of shipping.
 *
 * Both ramps are checked. A dark theme that nobody measured is the usual way a
 * component library claims AA and delivers it in one theme only.
 */

/** Read the declarations of the block that starts at `marker`. */
function block(marker: string): Record<string, string> {
  const start = CSS.indexOf(marker);
  expect(start, `${marker} is missing from tokens.css`).toBeGreaterThan(-1);
  let depth = 0;
  let index = start + marker.length - 1;
  const open = index;
  for (; index < CSS.length; index += 1) {
    if (CSS[index] === '{') depth += 1;
    else if (CSS[index] === '}') {
      depth -= 1;
      if (depth === 0) break;
    }
  }
  const body = CSS.slice(open + 1, index);
  const values: Record<string, string> = {};
  for (const match of body.matchAll(/(--jg-[a-z0-9-]+)\s*:\s*([^;]+);/g)) {
    const name = match[1];
    const value = match[2];
    if (name !== undefined && value !== undefined) values[name] = value.trim();
  }
  return values;
}

const LIGHT = block(':root {');
const DARK_ATTRIBUTE = block(":root[data-jg-theme='dark'] {");
const DARK_MEDIA = block(":root:not([data-jg-theme='light']) {");
const REDUCED = block('@media (prefers-reduced-motion: reduce) {\n  :root {');

/** sRGB relative luminance, WCAG 2.2 definition. */
function luminance(hex: string): number {
  const value = hex.trim().replace('#', '');
  const full =
    value.length === 3
      ? value
          .split('')
          .map((character) => character + character)
          .join('')
      : value;
  const channels = [0, 2, 4].map((offset) => {
    const part = Number.parseInt(full.slice(offset, offset + 2), 16) / 255;
    return part <= 0.04045 ? part / 12.92 : ((part + 0.055) / 1.055) ** 2.4;
  });
  const [r, g, b] = channels as [number, number, number];
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}

function contrast(foreground: string, background: string): number {
  const a = luminance(foreground);
  const b = luminance(background);
  const [light, dark] = a > b ? [a, b] : [b, a];
  return (light + 0.05) / (dark + 0.05);
}

/**
 * Text pairings, at 4.5:1 (WCAG 1.4.3).
 *
 * Every one of these is a pairing a component actually makes. Adding a colour
 * combination to a stylesheet means adding it here; that is the contract, and
 * it is why the list is explicit rather than a cartesian product of the ramp.
 */
const TEXT_PAIRS: [foreground: string, background: string][] = [
  ['--jg-text', '--jg-surface'],
  ['--jg-text', '--jg-surface-raised'],
  ['--jg-text', '--jg-surface-sunken'],
  ['--jg-text', '--jg-accent-subtle'],
  ['--jg-text', '--jg-danger-subtle'],
  ['--jg-text', '--jg-warning-subtle'],
  ['--jg-text', '--jg-success-subtle'],
  ['--jg-text', '--jg-info-subtle'],
  ['--jg-text-muted', '--jg-surface'],
  ['--jg-text-muted', '--jg-surface-raised'],
  ['--jg-text-muted', '--jg-surface-sunken'],
  ['--jg-text-subtle', '--jg-surface'],
  ['--jg-text-subtle', '--jg-surface-raised'],
  ['--jg-text-subtle', '--jg-surface-sunken'],
  ['--jg-text-inverse', '--jg-surface-inverse'],
  ['--jg-text-on-accent', '--jg-accent'],
  ['--jg-text-on-accent', '--jg-accent-hover'],
  ['--jg-text-on-accent', '--jg-accent-active'],
  ['--jg-text-on-accent', '--jg-danger'],
  ['--jg-text-on-accent', '--jg-danger-hover'],
  ['--jg-accent', '--jg-surface'],
  ['--jg-accent', '--jg-surface-raised'],
  ['--jg-accent', '--jg-surface-sunken'],
  ['--jg-danger', '--jg-surface'],
  ['--jg-danger', '--jg-surface-raised'],
  ['--jg-danger', '--jg-danger-subtle'],
  ['--jg-warning', '--jg-surface'],
  ['--jg-warning', '--jg-surface-raised'],
  ['--jg-warning', '--jg-warning-subtle'],
  ['--jg-success', '--jg-surface'],
  ['--jg-success', '--jg-surface-raised'],
  ['--jg-success', '--jg-success-subtle'],
  ['--jg-info', '--jg-surface'],
  ['--jg-info', '--jg-info-subtle'],
];

/**
 * Non-text pairings, at 3:1 (WCAG 1.4.11).
 *
 * A control's boundary is what tells a low-vision user that a control is
 * there. `--jg-border` draws the edge of every input, select and table, so it
 * is held to 3:1; `--jg-border-subtle` divides content that is legible without
 * it and is not.
 */
const BOUNDARY_PAIRS: [foreground: string, background: string][] = [
  ['--jg-border', '--jg-surface'],
  ['--jg-border', '--jg-surface-raised'],
  ['--jg-border-strong', '--jg-surface'],
  ['--jg-border-strong', '--jg-surface-raised'],
  ['--jg-border', '--jg-surface-sunken'],
  ['--jg-focus-ring', '--jg-surface'],
  ['--jg-focus-ring', '--jg-surface-raised'],
  ['--jg-focus-ring', '--jg-surface-sunken'],
];

/** Look a token up, failing with the ramp's name rather than `undefined`. */
function defined(ramp: Record<string, string>, token: string, rampName: string): string {
  const value = ramp[token];
  if (value === undefined) {
    throw new Error(`${token} is not defined in the ${rampName} ramp`);
  }
  return value;
}

const RAMPS: [name: string, ramp: Record<string, string>][] = [
  ['light', LIGHT],
  ['dark', DARK_ATTRIBUTE],
];

describe.each(RAMPS)('the %s ramp', (name, ramp) => {
  it.each(TEXT_PAIRS)('has %s on %s at 4.5:1 or better', (foreground, background) => {
    const fg = defined(ramp, foreground, name);
    const bg = defined(ramp, background, name);
    const ratio = contrast(fg, bg);
    expect(
      Number(ratio.toFixed(2)),
      `${foreground} (${fg}) on ${background} (${bg}) is ${ratio.toFixed(2)}:1`,
    ).toBeGreaterThanOrEqual(4.5);
  });

  it.each(BOUNDARY_PAIRS)('has %s on %s at 3:1 or better', (foreground, background) => {
    const fg = defined(ramp, foreground, name);
    const bg = defined(ramp, background, name);
    const ratio = contrast(fg, bg);
    expect(
      Number(ratio.toFixed(2)),
      `${foreground} (${fg}) on ${background} (${bg}) is ${ratio.toFixed(2)}:1`,
    ).toBeGreaterThanOrEqual(3);
  });
});

describe('the two ramps', () => {
  it('define the same tokens', () => {
    expect(Object.keys(DARK_ATTRIBUTE).sort()).toEqual(
      Object.keys(LIGHT)
        .filter((token) => token in DARK_ATTRIBUTE || isThemed(token))
        .sort(),
    );
  });

  /**
   * The dark ramp is written twice -- once under the media query, once under
   * the explicit attribute -- because the attribute has to win in both
   * directions. Two copies drift, so the copies are compared.
   */
  it('state the dark values identically in the media query and the attribute', () => {
    expect(DARK_MEDIA).toEqual(DARK_ATTRIBUTE);
  });
});

/**
 * Whether a light-ramp token is a colour the dark ramp must restate.
 *
 * Prefix matching alone is wrong: `--jg-text-2xl` is a font size that happens
 * to start with `--jg-text`, and `--jg-border-width` is a length. So the
 * colour-bearing names are matched exactly.
 */
function isThemed(token: string): boolean {
  return /^--jg-(?:surface|text|border|accent|danger|warning|success|info|focus-ring|scrim|elevation)(?:-(?:raised|sunken|overlay|inverse|muted|subtle|on-accent|strong|border|hover|active|1|2|3))?$/.test(
    token,
  );
}

describe('reduced motion (AC-U9)', () => {
  it('zeroes every duration token', () => {
    const durations = Object.keys(LIGHT).filter((token) => token.startsWith('--jg-duration-'));
    expect(durations.length).toBeGreaterThan(0);
    for (const token of durations) {
      expect(REDUCED[token], `${token} is not zeroed under prefers-reduced-motion`).toBe('0ms');
    }
  });

  it('caps animation and transition duration globally', () => {
    expect(CSS).toMatch(/animation-duration:\s*0\.01ms\s*!important/);
    expect(CSS).toMatch(/transition-duration:\s*0\.01ms\s*!important/);
  });
});

describe('the focus indicator (WCAG 2.4.7)', () => {
  it('is never removed without a replacement', () => {
    // `outline: none` with nothing in its place is the single most common way
    // a component library loses its focus indicator, so it is banned outright.
    expect(CSS).not.toMatch(/outline:\s*(none|0)\s*;/);
  });

  it('is defined once, on :focus-visible', () => {
    expect(CSS).toMatch(/\.jg-root :focus-visible\s*\{[^}]*outline:\s*var\(--jg-focus-width\)/);
  });
});
