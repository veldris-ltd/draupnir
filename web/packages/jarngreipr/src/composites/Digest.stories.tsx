import type { Meta } from '@storybook/react';
import { COMPONENT_META, SAMPLE_PROBLEM, stateStories } from '../state/stories';
import { Digest } from '.';

export default {
  title: 'Composites/Digest',
  ...COMPONENT_META,
} satisfies Meta;

const stories = stateStories((state) => (
  <Digest
    value="9f2c4a10bd7e5583c1a04e6f9b28d3711ce4a0f5628d1b937ac4e5d0f16b8a23"
    of="adapter"
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
