import type { Meta } from '@storybook/react';
import { COMPONENT_META, SAMPLE_PROBLEM, stateStories } from '../state/stories';
import { Tooltip } from '.';

// A literal, because Storybook's story indexer is static: a default export it
// cannot read at parse time is a component that never appears in the sidebar.
export default {
  title: 'Primitives/Tooltip',
  ...COMPONENT_META,
} satisfies Meta;

const stories = stateStories((state) => (
  <Tooltip
    content="The digest of the evaluation report GLEIPNIR judged."
    state={state}
    problem={SAMPLE_PROBLEM}
  >
    <span>Evidence</span>
  </Tooltip>
));

export const Ready = stories.Ready;
export const Loading = stories.Loading;
export const Empty = stories.Empty;
export const ErrorState = stories.ErrorState;
export const Denied = stories.Denied;
export const ReadOnly = stories.ReadOnly;
export const Partitioned = stories.Partitioned;
