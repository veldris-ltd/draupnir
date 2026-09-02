import type { Meta } from '@storybook/react';
import { COMPONENT_META, SAMPLE_PROBLEM, stateStories } from '../state/stories';
import { CapacityGauge } from '.';

// A literal, because Storybook's story indexer is static: a default export it
// cannot read at parse time is a component that never appears in the sidebar.
export default {
  title: 'Composites/Capacity gauge',
  ...COMPONENT_META,
} satisfies Meta;

const stories = stateStories((state) => (
  <CapacityGauge
    label="Edinburgh GPU pool"
    used={432}
    total={512}
    reserved={48}
    unit="GPUs"
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
