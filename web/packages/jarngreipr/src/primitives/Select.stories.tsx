import type { Meta } from '@storybook/react';
import { COMPONENT_META, SAMPLE_PROBLEM, stateStories } from '../state/stories';
import { Select } from '.';

// A literal, because Storybook's story indexer is static: a default export it
// cannot read at parse time is a component that never appears in the sidebar.
export default {
  title: 'Primitives/Select',
  ...COMPONENT_META,
} satisfies Meta;

const TIERS = [
  { value: 'tier-a', label: 'Tier A — member states' },
  { value: 'tier-b', label: 'Tier B — observers' },
];

const stories = stateStories((state) => (
  <Select
    label="Tier"
    options={TIERS}
    value="tier-a"
    hint="Tier A models are released to member states."
    state={state}
    problem={SAMPLE_PROBLEM}
  />
));

export const Ready = stories.Ready;
export const Loading = stories.Loading;
export const Empty = stories.Empty;
export const ErrorState = stories.ErrorState;
export const Denied = stories.Denied;
export const ReadOnly = stories.ReadOnly;
export const Partitioned = stories.Partitioned;
