import type { Meta } from '@storybook/react';
import { COMPONENT_META, SAMPLE_PROBLEM, stateStories } from '../state/stories';
import { DiffViewer } from '.';

// A literal, because Storybook's story indexer is static: a default export it
// cannot read at parse time is a component that never appears in the sidebar.
export default {
  title: 'Composites/Diff viewer',
  ...COMPONENT_META,
} satisfies Meta;

const LINES = [
  { op: 'hunk' as const, text: '@@ release.tier-a @@' },
  { op: 'context' as const, oldNumber: 11, newNumber: 11, text: '  requires:' },
  { op: 'remove' as const, oldNumber: 12, text: '    min_aggregate_score: 0.70' },
  { op: 'add' as const, newNumber: 12, text: '    min_aggregate_score: 0.72' },
  { op: 'context' as const, oldNumber: 13, newNumber: 13, text: '    model_card: signed' },
  { op: 'add' as const, newNumber: 14, text: '    container_scan: no_critical' },
];

const stories = stateStories((state) => (
  <DiffViewer
    fromLabel="policy 2026.08.2"
    toLabel="policy 2026.08.3"
    lines={LINES}
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
