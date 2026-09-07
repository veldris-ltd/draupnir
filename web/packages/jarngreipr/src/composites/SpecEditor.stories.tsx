import type { Meta } from '@storybook/react';
import { COMPONENT_META, SAMPLE_PROBLEM, stateStories } from '../state/stories';
import { SpecEditor, type SpecProblem } from '.';

export default {
  title: 'Composites/SpecEditor',
  ...COMPONENT_META,
} satisfies Meta;

const SPEC = JSON.stringify(
  {
    apiVersion: 'draupnir/v1',
    kind: 'AdapterRun',
    metadata: { name: 'cim-gbr-v1.0', jurisdiction: 'GBR' },
    train: { driver: 'hamarr.llamafactory/v1', params: { rank: 64, alpha: 128 } },
  },
  null,
  2,
);

/**
 * A stand-in for the compiled schema the console passes in. It checks one rule
 * so the story shows a real problem rather than an empty list.
 */
function validate(parsed: unknown): SpecProblem[] {
  const document = parsed as { train?: { params?: { rank?: number } } };
  const rank = document.train?.params?.rank;
  if (typeof rank !== 'number') {
    return [{ pointer: '/train/params/rank', message: 'is required and must be a number.' }];
  }
  return [];
}

const stories = stateStories((state) => (
  <SpecEditor
    label="Run specification"
    value={SPEC}
    validate={validate}
    dryRunResult="Dry run: the plan places one element on the adapters partition."
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
