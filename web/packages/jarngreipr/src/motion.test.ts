import { describe, expect, it } from 'vitest';
import { readSource } from '../../../tests/source';

/**
 * AC-U9: `prefers-reduced-motion` is respected across every animated
 * component.
 *
 * The token layer zeroes every duration token under the preference, and
 * `tokens.test.ts` checks that. This checks the other half: that no component
 * animates outside the token layer without saying what happens when a user has
 * asked for less motion. A spinner written as `animation: jg-spin 900ms` is
 * invisible to the token override -- the global
 * `animation-duration: 0.01ms !important` would freeze it mid-rotation rather
 * than stop it -- so such a rule has to carry its own reduced-motion answer.
 *
 * Two acceptable answers, and no third:
 *
 *   1. the duration is a `var(--jg-duration-*)` token, or
 *   2. the same selector appears under `prefers-reduced-motion: reduce` in the
 *      same stylesheet, turning the animation off.
 */

const STYLESHEETS = [
  ['state', 'packages/jarngreipr/src/state/states.css'],
  ['primitives', 'packages/jarngreipr/src/primitives/primitives.css'],
  ['composites', 'packages/jarngreipr/src/composites/composites.css'],
] as const;

const ANIMATED = new Set(['animation', 'animation-duration', 'transition', 'transition-duration']);

interface Rule {
  selectors: string[];
  property: string;
  value: string;
  reducedMotion: boolean;
}

/** Read a stylesheet into flat rules, noting which sit under the preference. */
function rules(css: string): Rule[] {
  const source = css.replace(/\/\*[\s\S]*?\*\//g, ' ');
  const found: Rule[] = [];
  const stack: string[] = [];
  let buffer = '';

  for (const character of source) {
    if (character === '{') {
      stack.push(buffer.trim());
      buffer = '';
    } else if (character === '}') {
      flush();
      stack.pop();
    } else if (character === ';') {
      flush();
    } else {
      buffer += character;
    }
  }
  return found;

  function flush(): void {
    const text = buffer.trim();
    buffer = '';
    const colon = text.indexOf(':');
    if (colon <= 0) return;
    const property = text.slice(0, colon).trim().toLowerCase();
    if (!ANIMATED.has(property)) return;
    const selector = stack[stack.length - 1];
    if (selector === undefined) return;
    found.push({
      selectors: selector.split(',').map((part) => part.trim()),
      property,
      value: text.slice(colon + 1).trim(),
      reducedMotion: stack.some((level) => level.includes('prefers-reduced-motion')),
    });
  }
}

describe.each(STYLESHEETS)('the %s stylesheet', (_name, path) => {
  const all = rules(readSource(path));

  /** Selectors that are switched off under the preference, in this file. */
  const stilled = new Set(
    all
      .filter((rule) => rule.reducedMotion && /^(none|0m?s)\b/.test(rule.value))
      .flatMap((rule) => rule.selectors),
  );

  const animated = all.filter((rule) => !rule.reducedMotion);

  it('animates something (otherwise this test proves nothing)', () => {
    // A stylesheet with no animation would pass every assertion below
    // vacuously, which is the failure mode this whole file exists to avoid.
    expect(animated.length).toBeGreaterThan(0);
  });

  it.each(animated.map((rule) => [rule.selectors.join(', '), rule.property, rule] as const))(
    '%s { %s } respects prefers-reduced-motion',
    (_selector, _property, rule) => {
      const tokenised = rule.value.includes('var(--jg-duration-');
      const overridden = rule.selectors.every((selector) => stilled.has(selector));
      expect(
        tokenised || overridden,
        `\`${rule.property}: ${rule.value}\` on \`${rule.selectors.join(', ')}\` states a ` +
          'duration outside the token layer and has no prefers-reduced-motion rule. Use a ' +
          '--jg-duration-* token, or switch the animation off under the preference.',
      ).toBe(true);
    },
  );
});
