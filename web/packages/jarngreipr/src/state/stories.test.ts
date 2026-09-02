import type { JSX } from 'react';
import { readdirSync } from 'node:fs';
import { join } from 'node:path';
import { describe, expect, it } from 'vitest';
import { readSource, workspaceRoot } from '../../../../tests/source';
import { ALL_STATES } from './states';
import { stateStories } from './stories';

/**
 * The other half of AC-U2.
 *
 * `stateStories()` makes the seven stories easy to produce; this makes
 * producing fewer than seven impossible to merge. Without it the rule is
 * "every component ships six states, honestly", which is the kind of rule that
 * holds for nineteen components and quietly stops at the twentieth.
 *
 * The check is deliberately structural rather than a runtime import of every
 * story module: Storybook's indexer is itself static, and a story it cannot
 * see statically is a story that never renders and never gets a snapshot. So
 * the test asserts exactly what the indexer asserts -- named exports in the
 * file -- rather than what happens to exist after evaluation.
 */

const SRC = join(workspaceRoot(), 'packages', 'jarngreipr', 'src');

/** The seven export names Storybook will index, in story order. */
const EXPECTED_EXPORTS = [
  'Ready',
  'Loading',
  'Empty',
  'ErrorState',
  'Denied',
  'ReadOnly',
  'Partitioned',
];

/** Component names exported from a layer's barrel, by their `export {}` block. */
function exportedComponents(layer: 'primitives' | 'composites'): string[] {
  const index = readSource('packages/jarngreipr/src/index.ts');
  const block = new RegExp(`export \\{([^}]*)\\} from '\\./${layer}';`).exec(index);
  expect(block, `index.ts has no value export block for ./${layer}`).not.toBeNull();
  return (block?.[1] ?? '')
    .split(',')
    .map((name) => name.trim())
    .filter((name) => /^[A-Z]/.test(name));
}

function storyFiles(layer: 'primitives' | 'composites'): string[] {
  return readdirSync(join(SRC, layer)).filter((name) => name.endsWith('.stories.tsx'));
}

describe.each(['primitives', 'composites'] as const)('every %s component', (layer) => {
  const components = exportedComponents(layer);

  it('is exported', () => {
    expect(components.length).toBeGreaterThan(0);
  });

  it.each(components)('%s has a story file', (component) => {
    expect(storyFiles(layer)).toContain(`${component}.stories.tsx`);
  });

  it.each(components)('%s has a story for every state', (component) => {
    const source = readSource(`packages/jarngreipr/src/${layer}/${component}.stories.tsx`);
    for (const name of EXPECTED_EXPORTS) {
      expect(
        source,
        `${component}.stories.tsx does not export ${name}; a component with only a happy path is not done`,
      ).toContain(`export const ${name} =`);
    }
  });

  it.each(components)('%s renders its stories through the shared factory', (component) => {
    // Bypassing the factory is how the seven stories drift into six good ones
    // and one that renders `ready` under a different name. The shared meta
    // goes with it, so every component page carries the same a11y run.
    const source = readSource(`packages/jarngreipr/src/${layer}/${component}.stories.tsx`);
    expect(source).toContain('stateStories(');
    expect(source).toContain('...COMPONENT_META');
  });

  it('has no story file without a component', () => {
    const orphans = storyFiles(layer)
      .map((file) => file.replace('.stories.tsx', ''))
      .filter((name) => !components.includes(name));
    expect(orphans).toEqual([]);
  });
});

/** A stand-in element: the factory is under test, not what it renders. */
const EMPTY = null as unknown as JSX.Element;

describe('the story factory', () => {
  it('produces one story per state', () => {
    const stories = stateStories(() => EMPTY);
    expect(Object.keys(stories).sort()).toEqual([...EXPECTED_EXPORTS].sort());
    expect(Object.keys(stories)).toHaveLength(ALL_STATES.length);
  });

  it('names the error story Error rather than ErrorState', () => {
    // `Error` is a reserved global and cannot be a const declaration, so the
    // export is `ErrorState` and the displayed name is corrected here. Without
    // this the sidebar would read "Error State", which is not a state.
    const stories = stateStories(() => EMPTY);
    expect(stories.ErrorState?.name).toBe('Error');
  });

  it('tags each story with the state it renders', () => {
    const stories = stateStories(() => EMPTY);
    expect(stories.Partitioned?.parameters).toMatchObject({
      jarngreipr: { state: 'partitioned' },
    });
  });
});
