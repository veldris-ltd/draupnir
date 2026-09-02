import type { Meta } from '@storybook/react';
import { COMPONENT_META, SAMPLE_PROBLEM, stateStories } from '../state/stories';
import { Drawer } from '.';

// A literal, because Storybook's story indexer is static: a default export it
// cannot read at parse time is a component that never appears in the sidebar.
export default {
  title: 'Primitives/Drawer',
  ...COMPONENT_META,
} satisfies Meta;

const stories = stateStories((state) => (
  <Drawer
    title="Run 01a06244-ad82"
    onClose={() => undefined}
    state={state}
    problem={SAMPLE_PROBLEM}
  >
    <p>Submitted by j.webb-benjamin at 09:14 UTC.</p>
  </Drawer>
));

export const Ready = stories.Ready;
export const Loading = stories.Loading;
export const Empty = stories.Empty;
export const ErrorState = stories.ErrorState;
export const Denied = stories.Denied;
export const ReadOnly = stories.ReadOnly;
export const Partitioned = stories.Partitioned;
