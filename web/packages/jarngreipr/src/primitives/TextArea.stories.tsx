import type { Meta } from '@storybook/react';
import { COMPONENT_META, SAMPLE_PROBLEM, stateStories } from '../state/stories';
import { TextArea } from '.';

export default {
  title: 'Primitives/TextArea',
  ...COMPONENT_META,
} satisfies Meta;

const stories = stateStories((state) => (
  <TextArea
    label="Requeue reason"
    hint="Stated in the ledger entry, so write it for whoever reads the chain."
    placeholder="E1 margin was 0.004 below tolerance"
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
