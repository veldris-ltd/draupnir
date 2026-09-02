import type { Meta } from '@storybook/react';
import { COMPONENT_META, SAMPLE_PROBLEM, stateStories } from '../state/stories';
import { SweepMatrix } from '.';

// A literal, because Storybook's story indexer is static: a default export it
// cannot read at parse time is a component that never appears in the sidebar.
export default {
  title: 'Composites/Sweep comparison matrix',
  ...COMPONENT_META,
} satisfies Meta;

const METRICS = [
  { key: 'score', label: 'Aggregate', higherIsBetter: true },
  { key: 'loss', label: 'Eval loss', higherIsBetter: false },
  { key: 'hours', label: 'GPU time', higherIsBetter: false, unit: 'h' },
];

const ARMS = [
  { id: 'a', label: 'lr 1e-4', values: { score: 0.771, loss: 1.94, hours: 312 } },
  { id: 'b', label: 'lr 3e-4', values: { score: 0.781, loss: 1.88, hours: 318 } },
  { id: 'c', label: 'lr 1e-3', values: { score: 0.742, loss: 2.11, hours: undefined } },
];

const stories = stateStories((state) => (
  <SweepMatrix
    caption="Learning-rate sweep, CIM-014"
    metrics={METRICS}
    arms={ARMS}
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
