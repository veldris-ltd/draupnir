import type { Meta } from '@storybook/react';
import { COMPONENT_META, SAMPLE_PROBLEM, stateStories } from '../state/stories';
import { GateCard } from '.';

// A literal, because Storybook's story indexer is static: a default export it
// cannot read at parse time is a component that never appears in the sidebar.
export default {
  title: 'Composites/Gate card',
  ...COMPONENT_META,
} satisfies Meta;

const EVIDENCE = [
  {
    kind: 'eval-report',
    requirement: 'Aggregate score at or above 0.72',
    met: true,
    observed: '0.781',
    digest: 'sha256:9f2c1b7e4a0d6538bb1c9d2e5f70a4c83e6d1b09f7a2c45e8d3b6091fa2c7d4e',
  },
  {
    kind: 'scan',
    requirement: 'No critical findings in the container scan',
    met: false,
    observed: '1 critical',
    digest: 'sha256:31d8a7c05e6b942f18ac3d70b5e29f6410c8d2a7e93b4c15d0f8a627be934c10',
  },
  {
    kind: 'card',
    requirement: 'Model card present and signed',
    met: true,
    digest: 'sha256:5b0e93d21fa7c648e0d3b7192cf4a86d5e207bc931f8a4d60e2c95713ab60e8f',
  },
];

const stories = stateStories((state) => (
  <GateCard
    gate="release.tier-a"
    decision="deny"
    policyVersion="2026.08.3"
    evidence={EVIDENCE}
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
