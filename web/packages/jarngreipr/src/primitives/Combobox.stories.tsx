import type { Meta } from '@storybook/react';
import { COMPONENT_META, SAMPLE_PROBLEM, stateStories } from '../state/stories';
import { Combobox } from '.';

export default {
  title: 'Primitives/Combobox',
  ...COMPONENT_META,
} satisfies Meta;

// Fourteen, which is above the threshold section 5.1 sets for preferring a
// combobox to a native select. A shorter list would be the wrong component.
const JURISDICTIONS = [
  'Australia',
  'Bangladesh',
  'Canada',
  'Ghana',
  'India',
  'Jamaica',
  'Kenya',
  'Malaysia',
  'New Zealand',
  'Nigeria',
  'Pakistan',
  'Singapore',
  'South Africa',
  'United Kingdom',
].map((label) => ({ value: label.toLowerCase().replace(/ /g, '-'), label }));

const stories = stateStories((state) => (
  <Combobox
    label="Jurisdiction"
    hint="Type to filter. Fifty-six are registered."
    options={JURISDICTIONS}
    emptyMessage="No jurisdiction matches that. Clear the filter to see all of them."
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
