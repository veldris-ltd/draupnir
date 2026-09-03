import type { Meta } from '@storybook/react';
import { COMPONENT_META, SAMPLE_PROBLEM, stateStories } from '../state/stories';
import { PoolStatus } from './PoolStatus';

// A literal, because Storybook's story indexer is static: a default export it
// cannot read at parse time is a component that never appears in the sidebar.
export default {
  title: 'Example/PoolStatus',
  ...COMPONENT_META,
} satisfies Meta;

const SAMPLE = [
  { id: 'adapters', label: 'adapters', value: '18 / 24', tone: 'warning' },
  { id: 'merges', label: 'merges', value: '2 / 8', tone: 'success' },
  { id: 'export', label: 'export', value: '0 / 4', tone: 'neutral' },
] as const;

const stories = stateStories((state) => (
  <PoolStatus label="Allocation pools" items={[...SAMPLE]} state={state} problem={SAMPLE_PROBLEM} />
));

export const Ready = stories.Ready;
export const Loading = stories.Loading;
export const Empty = stories.Empty;
export const ErrorState = stories.ErrorState;
export const Denied = stories.Denied;
export const ReadOnly = stories.ReadOnly;
export const Partitioned = stories.Partitioned;
