import type { Meta } from '@storybook/react';
import { COMPONENT_META, SAMPLE_PROBLEM, stateStories } from '../state/stories';
import { Dialog } from '.';

// A literal, because Storybook's story indexer is static: a default export it
// cannot read at parse time is a component that never appears in the sidebar.
export default {
  title: 'Primitives/Dialog',
  ...COMPONENT_META,
} satisfies Meta;

const stories = stateStories((state) => (
  <Dialog
    title="Cancel this run?"
    consequence="The 42,000 steps trained so far are kept, the scheduler job is stopped, and the run cannot be resumed."
    confirmLabel="Cancel the run"
    onConfirm={() => undefined}
    onDismiss={() => undefined}
    state={state}
    problem={SAMPLE_PROBLEM}
  >
    <p>Run 01a06244-ad82 is training CIM-014 Gaelic.</p>
  </Dialog>
));

export const Ready = stories.Ready;
export const Loading = stories.Loading;
export const Empty = stories.Empty;
export const ErrorState = stories.ErrorState;
export const Denied = stories.Denied;
export const ReadOnly = stories.ReadOnly;
export const Partitioned = stories.Partitioned;
