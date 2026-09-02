import type { JSX } from 'react';
import type { Meta, StoryObj } from '@storybook/react';
import { ALL_STATES, type ComponentState, type ProblemSummary } from './states';

/**
 * The story factory. One call produces the seven stories AC-U2 requires.
 *
 * A component author writes one render function and gets `Ready`, `Loading`,
 * `Empty`, `Error`, `Denied`, `ReadOnly` and `Partitioned`. That is the point:
 * AC-U2 and AC-Q5 both turn on every component having a story per state, and a
 * rule that depends on an author remembering to write seven exports is a rule
 * with a hole in it by the twentieth component.
 *
 * `stateStories.test.ts` closes the other half, asserting that every exported
 * component has a story file whose stories cover every state -- so the factory
 * is the easy path rather than the only one, and skipping it is caught.
 */

/** The problem document the `error` story renders. Realistic, not lorem. */
export const SAMPLE_PROBLEM: ProblemSummary = {
  title: 'The run could not be cancelled',
  detail:
    'The run has already reached TRAINED and there is no scheduler job to stop. Re-read the run and retry if it is still what you intend.',
  code: 'precondition-failed',
  correlationId: '01a06244-ad82-7b67-af4d-8df67b2095e8',
};

/** A render function taking the state under test. */
export type StateRenderer = (state: ComponentState) => JSX.Element;

/** The stories one component ships, keyed by the export name Storybook uses. */
export type StateStories = Record<string, StoryObj>;

const EXPORT_NAME: Record<ComponentState, string> = {
  ready: 'Ready',
  loading: 'Loading',
  empty: 'Empty',
  error: 'ErrorState',
  denied: 'Denied',
  readOnly: 'ReadOnly',
  partitioned: 'Partitioned',
};

/**
 * Build the seven state stories for one component.
 *
 * Returned as an object the story module spreads into its exports, because
 * Storybook discovers stories by module export and there is no way to register
 * them dynamically that the static indexer can see.
 */
export function stateStories(render: StateRenderer): StateStories {
  const stories: StateStories = {};
  for (const state of ALL_STATES) {
    stories[EXPORT_NAME[state]] = {
      name: EXPORT_NAME[state] === 'ErrorState' ? 'Error' : EXPORT_NAME[state],
      render: () => render(state),
      parameters: { jarngreipr: { state } },
    };
  }
  return stories;
}

/**
 * The parameters every JARNGREIPR story file spreads into its default export.
 *
 * A constant rather than a function, because Storybook's story indexer reads
 * the default export statically: a `const meta = componentMeta(title)` parses
 * as a call, the indexer refuses it, and the component never appears. So each
 * story file writes an object literal and spreads this into it.
 *
 * `autodocs` so the component page is generated from the props and the
 * docstring rather than maintained beside them, and the a11y addon runs on
 * every story rather than on the ones somebody tagged.
 */
export const COMPONENT_META = {
  tags: ['autodocs'],
  parameters: {
    a11y: { config: { rules: [{ id: 'color-contrast', enabled: true }] } },
  },
} satisfies Omit<Meta, 'title'>;
