import type { Meta } from '@storybook/react';
import { COMPONENT_META, SAMPLE_PROBLEM, stateStories } from '../state/stories';
import { Tabs } from '.';

// A literal, because Storybook's story indexer is static: a default export it
// cannot read at parse time is a component that never appears in the sidebar.
export default {
  title: 'Primitives/Tabs',
  ...COMPONENT_META,
} satisfies Meta;

const ITEMS = [
  { id: 'overview', label: 'Overview', content: <p>CIM-014 Gaelic, step 42,000 of 120,000.</p> },
  { id: 'evidence', label: 'Evidence', content: <p>Three artefacts, all digested.</p> },
  { id: 'ledger', label: 'Ledger', content: <p>Eleven entries, chain intact.</p> },
];

const stories = stateStories((state) => (
  <Tabs
    label="Run detail"
    items={ITEMS}
    activeId="overview"
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
