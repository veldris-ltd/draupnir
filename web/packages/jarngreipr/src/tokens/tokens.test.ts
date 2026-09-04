import { describe, expect, it } from 'vitest';

import { readSource } from '../../../../tests/source';
import type { RunStateTokens, RunState as RunStateName } from './index';
import {
  ALL_PAIRS,
  COLOURED_RUN_STATES,
  DURATIONS,
  RAMPS,
  RUN_STATES,
  TYPE_ROLES,
  describeFailure,
  evaluate,
  resolve,
  runStateSlug,
  runStateTokens,
} from './index';

/**
 * The token layer, checked against VLD-UX-DRAUPNIR-001 section 4.
 *
 * Four criteria are settled here.
 *
 *   AC-V1  every token in section 4 exists, light and dark
 *   AC-V4  the run state mapping lives in the token layer and nowhere else
 *   AC-V6  both themes clear the same contrast thresholds, on every pair in use
 *   AC-V7  compact density never reduces the touch target
 *
 * The ramp values are compared against the specification's own table rather
 * than against a copy of themselves. That is the point of the first test: it
 * would fail if somebody improved a colour, which is the thing prompt UX-1
 * asks not to happen silently.
 */

const RAMP_CSS = readSource('packages/jarngreipr/src/tokens/ramp.css');
const TOKENS_CSS = readSource('packages/jarngreipr/src/tokens/tokens.css');
const STATE_CSS = readSource('packages/jarngreipr/src/tokens/state.css');
const DENSITY_CSS = readSource('packages/jarngreipr/src/tokens/density.css');

/** Every declaration inside the block whose header is exactly `selector`. */
function block(css: string, selector: string): Record<string, string> {
  const header = `${selector} {`;
  const start = css.indexOf(header);
  expect(start, `${selector} is missing`).toBeGreaterThan(-1);

  let depth = 0;
  let index = start + header.length - 1;
  const open = index;
  for (; index < css.length; index += 1) {
    if (css[index] === '{') depth += 1;
    else if (css[index] === '}') {
      depth -= 1;
      if (depth === 0) break;
    }
  }

  const body = css.slice(open + 1, index);
  const values: Record<string, string> = {};
  for (const match of body.matchAll(/(--jg-[a-z0-9-]+)\s*:\s*([^;]+);/g)) {
    const name = match[1];
    const value = match[2];
    if (name !== undefined && value !== undefined) {
      // Prettier wraps a long `color-mix(...)` across lines in one block and
      // not in its twin, so the two copies differ by whitespace and by nothing
      // else. Normalising here keeps the comparison about the values.
      values[name] = value
        .trim()
        .replace(/\s+/g, ' ')
        .replace(/\(\s+/g, '(')
        .replace(/\s+\)/g, ')');
    }
  }
  return values;
}

const RAMP = block(RAMP_CSS, ':root');
const LIGHT_ROLES = block(TOKENS_CSS, ':root');
const DARK_ROLES = block(TOKENS_CSS, ":root[data-jg-theme='dark']");
const DARK_ROLES_MEDIA = block(TOKENS_CSS, ":root:not([data-jg-theme='light'])");
const LIGHT_STATES = block(STATE_CSS, ':root');
const DARK_STATES = block(STATE_CSS, ":root[data-jg-theme='dark']");
const DARK_STATES_MEDIA = block(STATE_CSS, ":root:not([data-jg-theme='light'])");
const COMFORTABLE = block(DENSITY_CSS, ":root,\n:root[data-jg-density='comfortable']");
const COMPACT = block(DENSITY_CSS, ":root[data-jg-density='compact']");
const REDUCED = block(TOKENS_CSS, '@media (prefers-reduced-motion: reduce) {\n  :root');

/** A declaration that must be there, with the name in the failure if it is not. */
function must(declarations: Record<string, string>, token: string): string {
  const value = declarations[token];
  if (value === undefined) throw new Error(`${token} is not declared`);
  return value;
}

/** One ramp's steps, by number. */
function ramp(name: string): Record<number, string> {
  const found = SECTION_4_1[name];
  if (found === undefined) throw new Error(`${name} is not a ramp`);
  return found;
}

const LIGHT = { ...RAMP, ...LIGHT_ROLES, ...LIGHT_STATES };
const DARK = { ...LIGHT, ...DARK_ROLES, ...DARK_STATES };
const THEMES: [name: string, declarations: Record<string, string>][] = [
  ['light', LIGHT],
  ['dark', DARK],
];

// ---------------------------------------------------------------------------
// AC-V1: every token in section 4 exists
// ---------------------------------------------------------------------------

/** Section 4.1, transcribed from the specification and compared, not copied. */
const SECTION_4_1: Record<string, Record<number, string>> = {
  ink: {
    900: '#0e1a2b',
    800: '#1f3350',
    700: '#2a4a6b',
    600: '#3e5c82',
    400: '#7e8fa3',
    300: '#b9c6d4',
    100: '#e9f0f7',
    50: '#f5f8fb',
  },
  forge: {
    800: '#8a5a08',
    700: '#c6851c',
    500: '#e0a030',
    300: '#efc272',
    100: '#fbefd6',
    50: '#fdf7ec',
  },
  success: { 800: '#1b5e3a', 500: '#2e8b57', 300: '#7cc49b', 50: '#eaf4ee' },
  warning: { 800: '#8a5a08', 500: '#d98e04', 300: '#f0c15c', 50: '#fbf3e0' },
  danger: { 800: '#7a2a1f', 500: '#b3402f', 300: '#dc8c80', 50: '#fbf2f0' },
  info: { 800: '#0f5760', 500: '#177e89', 300: '#7dbfc6', 50: '#f2fafb' },
  merge: { 500: '#6c5b9e', 100: '#e8e2f5', 50: '#f4f1fa' },
};

describe('the ramp (section 4.1, AC-V1)', () => {
  it('has the seven ramps the specification names, and no eighth', () => {
    expect(Object.keys(RAMPS).sort()).toEqual(Object.keys(SECTION_4_1).sort());
  });

  for (const [ramp, steps] of Object.entries(SECTION_4_1)) {
    for (const [step, value] of Object.entries(steps)) {
      it(`has ${ramp} ${step} as ${value}, exactly`, () => {
        expect(RAMP[`--jg-${ramp}-${step}`]).toBe(value);
      });
    }
  }

  it('has the three dark surfaces of section 4.1', () => {
    expect(RAMP['--jg-dark-base']).toBe('#0b1420');
    expect(RAMP['--jg-dark-raised']).toBe('#14202f');
    expect(RAMP['--jg-dark-overlay']).toBe('#1d2c3e');
  });

  it('states the dark primary and secondary text as ink 100 and ink 400', () => {
    // Section 4.1: "primary text #E9F0F7, secondary text #7E8FA3". Both are ink
    // steps, so the dark theme names them rather than restating the hex.
    expect(resolve('--jg-text', DARK)).toBe(ramp('ink')[100]);
    expect(resolve('--jg-text-subtle', DARK)).toBe(ramp('ink')[400]);
  });
});

describe('typography (section 4.3, AC-V1)', () => {
  /** role -> [size in px, line height in px, weight]. */
  const SECTION_4_3: Record<string, [number, number, number]> = {
    display: [32, 40, 600],
    h1: [24, 32, 600],
    h2: [20, 28, 600],
    h3: [16, 24, 600],
    body: [14, 22, 400],
    small: [13, 20, 400],
    caption: [12, 16, 400],
    mono: [13, 20, 400],
  };

  const rem = (value: string): number => Number.parseFloat(value.replace('rem', '')) * 16;

  it('defines the eight roles and no others', () => {
    expect([...TYPE_ROLES].sort()).toEqual(Object.keys(SECTION_4_3).sort());
  });

  for (const [role, [size, line, weight]] of Object.entries(SECTION_4_3)) {
    it(`has ${role} at ${String(size)}/${String(line)} weight ${String(weight)}`, () => {
      const tokens = runTypeTokens(role);
      expect(rem(must(LIGHT_ROLES, tokens.size))).toBeCloseTo(size, 3);
      expect(rem(must(LIGHT_ROLES, tokens.line))).toBeCloseTo(line, 3);
      expect(LIGHT_ROLES[tokens.weight]).toBe(String(weight));
    });
  }

  it('names Inter for interface text and JetBrains Mono for values', () => {
    expect(LIGHT_ROLES['--jg-font-sans']).toMatch(/^Inter,/);
    expect(LIGHT_ROLES['--jg-font-mono']).toMatch(/^'JetBrains Mono',/);
  });
});

function runTypeTokens(role: string): { size: string; line: string; weight: string } {
  return {
    size: `--jg-text-${role}`,
    line: `--jg-text-${role}-line`,
    weight: `--jg-text-${role}-weight`,
  };
}

describe('space, radius, elevation and motion (section 4.4, AC-V1)', () => {
  it('has exactly the space scale 4, 8, 12, 16, 24, 32, 48, 64', () => {
    const scale = Object.entries(LIGHT_ROLES)
      .filter(([name, value]) => /^--jg-space-\d+$/.test(name) && value.endsWith('rem'))
      .map(([, value]) => Number.parseFloat(value) * 16)
      .sort((a, b) => a - b);
    expect(scale).toEqual([4, 8, 12, 16, 24, 32, 48, 64]);
  });

  it('has the five radii section 4.4 names by use', () => {
    expect(LIGHT_ROLES['--jg-radius-input']).toBe('2px');
    expect(LIGHT_ROLES['--jg-radius-card']).toBe('4px');
    expect(LIGHT_ROLES['--jg-radius-panel']).toBe('8px');
    expect(LIGHT_ROLES['--jg-radius-dialog']).toBe('12px');
    expect(LIGHT_ROLES['--jg-radius-badge']).toBe('9999px');
  });

  it('has three elevation levels and no fourth', () => {
    const levels = Object.keys({ ...LIGHT_ROLES }).filter((name) =>
      /^--jg-elevation-(flat|card|overlay)$/.test(name),
    );
    expect(levels.sort()).toEqual([
      '--jg-elevation-card',
      '--jg-elevation-flat',
      '--jg-elevation-overlay',
    ]);
  });

  it('has 120, 200 and 320 milliseconds, and one easing', () => {
    expect(LIGHT_ROLES['--jg-duration-micro']).toBe('120ms');
    expect(LIGHT_ROLES['--jg-duration-standard']).toBe('200ms');
    expect(LIGHT_ROLES['--jg-duration-large']).toBe('320ms');
    expect(LIGHT_ROLES['--jg-ease-standard']).toBe('cubic-bezier(0.2, 0, 0, 1)');
  });

  it('suppresses every duration under prefers-reduced-motion', () => {
    for (const duration of DURATIONS) {
      expect(REDUCED[`--jg-duration-${duration}`], `${duration} is not suppressed`).toBe('0ms');
    }
    expect(TOKENS_CSS).toMatch(/animation-duration:\s*0\.01ms\s*!important/);
    expect(TOKENS_CSS).toMatch(/transition-duration:\s*0\.01ms\s*!important/);
  });
});

// ---------------------------------------------------------------------------
// AC-V4: the run state mapping lives in the token layer
// ---------------------------------------------------------------------------

describe('run state tokens (section 4.2, AC-V4)', () => {
  /** Section 4.2's table, as the specification writes it. */
  const SECTION_4_2: Record<string, [ramp: string, step: number]> = {
    QUEUED: ['ink', 600],
    TRAINING: ['forge', 500],
    TRAINED: ['forge', 300],
    EVALUATING: ['ink', 800],
    MERGED: ['merge', 500],
    QUANTISED: ['ink', 900],
    AWAITING_APPROVAL: ['warning', 500],
    RELEASED: ['success', 500],
    FAILED: ['danger', 500],
    QUARANTINED: ['danger', 800],
  };

  it('covers the ten states section 4.2 assigns a colour to', () => {
    expect([...COLOURED_RUN_STATES].sort()).toEqual(Object.keys(SECTION_4_2).sort());
  });

  for (const [state, [name, step]] of Object.entries(SECTION_4_2)) {
    it(`gives ${state} ${name} ${String(step)}, exactly`, () => {
      const token = `--jg-state-${runStateSlug(state as RunStateName)}`;
      expect(LIGHT_STATES[token]).toBe(`var(--jg-${name}-${String(step)})`);
      expect(resolve(token, LIGHT)).toBe(ramp(name)[step]);
    });
  }

  it('gives every state of SAD 6.1 the four tokens a pill needs', () => {
    for (const state of RUN_STATES) {
      const tokens: RunStateTokens = runStateTokens(state);
      for (const token of [tokens.colour, tokens.surface, tokens.on, tokens.border]) {
        expect(() => must(LIGHT, token), `${token} in the light theme`).not.toThrow();
        expect(() => must(DARK, token), `${token} in the dark theme`).not.toThrow();
      }
    }
  });

  it('maps the attribute to the four properties, once per state', () => {
    for (const state of RUN_STATES) {
      expect(STATE_CSS, `no mapping rule for ${state}`).toContain(`[data-jg-run-state='${state}']`);
    }
  });

  /**
   * The rule the section opens with. A component with its own state-to-colour
   * table is the defect AC-V4 exists to prevent, and it is checkable: a state
   * name next to a colour token, anywhere outside the token layer, fails.
   */
  it('is the only place a state name meets a colour', () => {
    const offenders: string[] = [];
    for (const file of [
      'packages/jarngreipr/src/composites/index.tsx',
      'packages/jarngreipr/src/composites/composites.css',
      'packages/jarngreipr/src/primitives/index.tsx',
      'packages/jarngreipr/src/primitives/primitives.css',
      'apps/console/src/console.css',
    ]) {
      const source = readSource(file);
      for (const state of RUN_STATES) {
        const pattern = new RegExp(
          `${state}[^\\n]{0,80}(--jg-(?:ink|forge|success|warning|danger|info|merge)-)`,
        );
        if (pattern.test(source)) offenders.push(`${file}: ${state}`);
      }
    }
    expect(offenders).toEqual([]);
  });
});

// ---------------------------------------------------------------------------
// AC-V6: both themes, every pair in use
// ---------------------------------------------------------------------------

describe.each(THEMES)('contrast in the %s theme (AC-V6)', (name, declarations) => {
  it(`clears the threshold on all ${String(ALL_PAIRS.length)} pairs in use`, () => {
    const failures = evaluate(declarations);
    expect(failures.map(describeFailure), `${name} theme`).toEqual([]);
  });
});

describe('the two themes', () => {
  it('state the dark values identically in the media query and the attribute', () => {
    expect(DARK_ROLES_MEDIA).toEqual(DARK_ROLES);
    expect(DARK_STATES_MEDIA).toEqual(DARK_STATES);
  });

  it('redefine every colour role the light theme sets', () => {
    const themed = Object.keys(LIGHT_ROLES).filter((token) => {
      try {
        return resolve(token, LIGHT).startsWith('#');
      } catch {
        return false;
      }
    });
    for (const token of themed) {
      expect(DARK_ROLES[token], `${token} is not redefined in the dark theme`).toBeDefined();
    }
  });
});

// ---------------------------------------------------------------------------
// AC-V7: compact never reduces the touch target
// ---------------------------------------------------------------------------

describe('density (section 4.5, AC-V7)', () => {
  const px = (value: string): number => Number.parseFloat(value);

  /** Follow a `var()` reference through the non-colour tokens. */
  const follow = (value: string): string => {
    const reference = /^var\(\s*(--[a-z0-9-]+)\s*\)$/.exec(value.trim());
    const name = reference?.[1];
    return name === undefined ? value : follow(LIGHT_ROLES[name] ?? value);
  };

  it('reduces row height, body size and gutter in compact', () => {
    expect(px(must(COMFORTABLE, '--jg-row-height'))).toBe(44);
    expect(px(must(COMPACT, '--jg-row-height'))).toBe(32);

    expect(COMFORTABLE['--jg-density-body']).toBe('var(--jg-text-body)');
    expect(COMPACT['--jg-density-body']).toBe('var(--jg-text-small)');

    const gutter = (mode: Record<string, string>): number =>
      Number.parseFloat(follow(must(mode, '--jg-gutter'))) * 16;
    expect(gutter(COMFORTABLE)).toBe(24);
    expect(gutter(COMPACT)).toBe(12);
  });

  it('holds the touch target at 44 px and lets no mode reduce it', () => {
    // Declared once, outside both mode blocks. A mode that mentioned it at all
    // would be a mode that could shrink it, so the check is that neither does.
    expect(block(DENSITY_CSS, ':root')['--jg-touch-target']).toBe('44px');
    expect(COMFORTABLE['--jg-touch-target']).toBeUndefined();
    expect(COMPACT['--jg-touch-target']).toBeUndefined();
  });

  it('applies the floor to the controls it ships', () => {
    expect(DENSITY_CSS).toMatch(/min-block-size:\s*var\(--jg-touch-target\)/);
    expect(DENSITY_CSS).toMatch(/min-inline-size:\s*var\(--jg-touch-target\)/);
  });
});

// ---------------------------------------------------------------------------
// The focus indicator, which is not AC-V but is lost the same way
// ---------------------------------------------------------------------------

describe('the focus indicator (WCAG 2.4.7)', () => {
  it('is never removed without a replacement', () => {
    expect(TOKENS_CSS).not.toMatch(/outline:\s*(none|0)\s*;/);
  });

  it('is defined once, on :focus-visible', () => {
    expect(TOKENS_CSS).toMatch(
      /\.jg-root :focus-visible\s*\{[^}]*outline:\s*var\(--jg-focus-width\)/,
    );
  });
});
