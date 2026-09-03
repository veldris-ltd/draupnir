import axe from 'axe-core';
import { render } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { ALL_STATES } from '../state/states';
import { PoolStatus } from './PoolStatus';

/**
 * AC-Q8 for `skills/jarngreipr-component`.
 *
 * The skill claims its output "must include all six states and pass the token
 * linter and the axe check". Two of those three are already checked elsewhere
 * -- `token-lint.mjs` walks the whole workspace, and `stories.test.ts` fails a
 * component whose story file does not export all seven states -- so this is the
 * third, and it is the one that had no cheap check before.
 *
 * Real axe, not an approximation. A structural test that looked for `role`
 * attributes would pass the thing axe actually catches: `aria-label` on a bare
 * `<span>`, which is prohibited because a generic element has no role that
 * supports a name. That defect shipped in four console screens and in
 * JARNGREIPR's sweep matrix and passed review for a whole prompt.
 *
 * The Storybook sweep runs axe over every story in a real browser and is the
 * stronger check; this runs in jsdom in the frontend stage, so a component that
 * is inaccessible in an obvious way fails in seconds rather than in the eight
 * shards of the a11y sweep.
 *
 * `color-contrast` cannot run in jsdom -- it needs layout -- so axe reports it
 * as incomplete rather than passing. That is what the Storybook sweep is for,
 * and what `tokens.test.ts` measures directly from the ramp.
 */

const SAMPLE = [
  { id: 'adapters', label: 'adapters', value: '18 / 24', tone: 'warning' as const },
  { id: 'merges', label: 'merges', value: '2 / 8', tone: 'success' as const },
];

describe('the scaffolded component', () => {
  it.each(ALL_STATES)('has no serious or critical axe violation in %s', async (state) => {
    const { container } = render(
      <PoolStatus label="Allocation pools" items={SAMPLE} state={state} />,
    );

    const results = await axe.run(container, {
      // jsdom has no layout, so contrast is reported incomplete either way.
      // It is measured against the ramp in `tokens.test.ts` and in a browser
      // by the Storybook sweep.
      rules: { 'color-contrast': { enabled: false } },
    });
    const serious = results.violations.filter(
      (violation) => violation.impact === 'serious' || violation.impact === 'critical',
    );

    expect(
      serious.map((violation) => `${violation.id}: ${violation.help}`),
      'a component with an accessibility defect is not done (Decision S13)',
    ).toEqual([]);
  });

  it('renders something distinct in each of the six non-ready states', () => {
    const rendered = new Map<string, string>();
    for (const state of ALL_STATES) {
      const { container, unmount } = render(
        <PoolStatus label="Allocation pools" items={SAMPLE} state={state} />,
      );
      rendered.set(state, container.textContent);
      unmount();
    }

    // Six states, six different things said. Collapsing any two of them loses
    // information an operator acts on -- `denied` read as `empty` is "there is
    // nothing here" when the truth is "you may not see it".
    expect(new Set(rendered.values()).size).toBe(ALL_STATES.length);
  });
});
