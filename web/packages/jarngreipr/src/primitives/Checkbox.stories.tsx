import type { Meta } from '@storybook/react';
import { COMPONENT_META, SAMPLE_PROBLEM, stateStories } from '../state/stories';
import { Checkbox } from '.';

// A literal, because Storybook's story indexer is static: a default export it
// cannot read at parse time is a component that never appears in the sidebar.
export default {
  title: 'Primitives/Checkbox',
  ...COMPONENT_META,
} satisfies Meta;

const stories = stateStories((state) => (
  <Checkbox
    label="Publish the model card with the release"
    checked
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
