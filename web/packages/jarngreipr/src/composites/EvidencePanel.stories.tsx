import type { Meta } from '@storybook/react';
import { COMPONENT_META, SAMPLE_PROBLEM, stateStories } from '../state/stories';
import { EvidencePanel, type EvidenceRow } from '.';

export default {
  title: 'Composites/EvidencePanel',
  ...COMPONENT_META,
} satisfies Meta;

// E1 to E6 of SAD 6.2. E5 is the tightest, and the default sort brings it to
// the top without anybody looking for it.
const ROWS: EvidenceRow[] = [
  {
    gate: 'E1',
    statement: 'general capability has not regressed',
    passed: true,
    measurement: { value: 0.8123, baseline: 0.8041, margin: 0.0082 },
  },
  {
    gate: 'E2',
    statement: 'jurisdiction capability improves',
    passed: true,
    measurement: { value: 0.7712, baseline: 0.7314, margin: 0.0398 },
  },
  {
    gate: 'E3',
    statement: 'instruction following has not regressed',
    passed: true,
    measurement: { value: 0.6904, baseline: 0.6688, margin: 0.0216 },
  },
  {
    gate: 'E4',
    statement: 'factual accuracy reaches the floor',
    passed: true,
    measurement: { value: 0.6431, baseline: 0.6, margin: 0.0431 },
  },
  {
    gate: 'E5',
    statement: 'refusal and safety behaviour has not regressed',
    passed: true,
    measurement: { value: 0.9107, baseline: 0.9098, margin: 0.0009 },
  },
  {
    gate: 'E6',
    statement: 'evaluation set contamination stays below the ceiling',
    passed: true,
    measurement: { value: 0.0004, baseline: 0.001, margin: -0.0006 },
  },
];

const stories = stateStories((state) => (
  <EvidencePanel
    suiteVersion="general-core/2026.01"
    rows={ROWS}
    artefactDigest="9f2c4a10bd7e5583c1a04e6f9b28d3711ce4a0f5628d1b937ac4e5d0f16b8a23"
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
