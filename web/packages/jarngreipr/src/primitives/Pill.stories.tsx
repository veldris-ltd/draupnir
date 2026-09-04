import type { Meta } from '@storybook/react';
import { COMPONENT_META, SAMPLE_PROBLEM, stateStories } from '../state/stories';
import { Pill } from '.';

export default {
  title: 'Primitives/Pill',
  ...COMPONENT_META,
} satisfies Meta;

// The lifecycle state and the component state are different things, and this
// is the story where that is easiest to confuse: `runState` is what the run is
// doing, `state` is whether this pill could be rendered at all.
const stories = stateStories((state) => (
  <Pill runState="TRAINING" state={state} problem={SAMPLE_PROBLEM} />
));

export const Ready = stories.Ready;
export const Loading = stories.Loading;
export const Empty = stories.Empty;
export const ErrorState = stories.ErrorState;
export const Denied = stories.Denied;
export const ReadOnly = stories.ReadOnly;
export const Partitioned = stories.Partitioned;
