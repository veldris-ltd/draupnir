import type { Meta } from '@storybook/react';
import { COMPONENT_META, SAMPLE_PROBLEM, stateStories } from '../state/stories';
import { LogViewer } from '.';

// A literal, because Storybook's story indexer is static: a default export it
// cannot read at parse time is a component that never appears in the sidebar.
export default {
  title: 'Composites/Log viewer',
  ...COMPONENT_META,
} satisfies Meta;

const LINES = Array.from({ length: 20_000 }, (_, index) => ({
  number: index + 1,
  text:
    index % 997 === 0
      ? `step ${String(index)} loss diverged, retrying from the last checkpoint`
      : `step ${String(index)} loss=${(2.4 - index / 20_000).toFixed(4)} lr=3.0e-4`,
  level: index % 997 === 0 ? ('error' as const) : ('info' as const),
}));

const stories = stateStories((state) => (
  <LogViewer
    label="Training log, run 01a06244-ad82"
    lines={LINES}
    streaming
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
