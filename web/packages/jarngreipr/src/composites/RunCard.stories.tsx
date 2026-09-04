import type { Meta } from '@storybook/react';
import { COMPONENT_META, SAMPLE_PROBLEM, stateStories } from '../state/stories';
import { RunCard } from '.';

// A literal, because Storybook's story indexer is static: a default export it
// cannot read at parse time is a component that never appears in the sidebar.
export default {
  title: 'Composites/Run card',
  ...COMPONENT_META,
} satisfies Meta;

const ACTIONS = [{ label: 'Open' }, { label: 'Cancel', variant: 'danger' as const }];

const stories = stateStories((state) => (
  <RunCard
    runId="01a06244-ad82-7b67-af4d-8df67b2095e8"
    model="CIM-014 Gaelic"
    runState="TRAINING"
    step={42_000}
    totalSteps={120_000}
    startedAt="2026-09-02 09:14 UTC"
    submittedBy="j.webb-benjamin"
    actions={ACTIONS}
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
