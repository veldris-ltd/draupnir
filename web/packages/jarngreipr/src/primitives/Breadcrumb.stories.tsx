import type { Meta } from '@storybook/react';
import { COMPONENT_META, SAMPLE_PROBLEM, stateStories } from '../state/stories';
import { Breadcrumb } from '.';

// A literal, because Storybook's story indexer is static: a default export it
// cannot read at parse time is a component that never appears in the sidebar.
export default {
  title: 'Primitives/Breadcrumb',
  ...COMPONENT_META,
} satisfies Meta;

const CRUMBS = [
  { label: 'Runs', href: '/runs' },
  { label: 'CIM-014 Gaelic', href: '/runs/cim-014' },
  { label: '01a06244-ad82' },
];

const stories = stateStories((state) => (
  <Breadcrumb items={CRUMBS} state={state} problem={SAMPLE_PROBLEM} />
));

export const Ready = stories.Ready;
export const Loading = stories.Loading;
export const Empty = stories.Empty;
export const ErrorState = stories.ErrorState;
export const Denied = stories.Denied;
export const ReadOnly = stories.ReadOnly;
export const Partitioned = stories.Partitioned;
