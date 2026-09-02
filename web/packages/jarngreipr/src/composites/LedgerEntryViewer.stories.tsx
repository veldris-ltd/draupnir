import type { Meta } from '@storybook/react';
import { COMPONENT_META, SAMPLE_PROBLEM, stateStories } from '../state/stories';
import { LedgerEntryViewer } from '.';

// A literal, because Storybook's story indexer is static: a default export it
// cannot read at parse time is a component that never appears in the sidebar.
export default {
  title: 'Composites/Ledger entry viewer',
  ...COMPONENT_META,
} satisfies Meta;

const ENTRY = {
  sequence: 4_812,
  kind: 'run.released',
  recordedAt: '2026-09-02 11:47:03 UTC',
  actor: 'a.stewart (release-approver)',
  digest: 'sha256:9f2c1b7e4a0d6538bb1c9d2e5f70a4c83e6d1b09f7a2c45e8d3b6091fa2c7d4e',
  previousDigest: 'sha256:31d8a7c05e6b942f18ac3d70b5e29f6410c8d2a7e93b4c15d0f8a627be934c10',
  payload:
    '{\n  "run": "01a06244-ad82-7b67-af4d-8df67b2095e8",\n  "model": "CIM-014 Gaelic",\n  "tier": "A",\n  "gate": "release.tier-a"\n}',
  signature: 'ed25519:MEUCIQD2n1kR8x0pQ7v6cT4mZ1yBw9jL5sH3aN0eK8fUqXtR',
  keyId: 'svalinn/release/2026-08',
  verified: true,
};

const stories = stateStories((state) => (
  <LedgerEntryViewer entry={ENTRY} state={state} problem={SAMPLE_PROBLEM} />
));

export const Ready = stories.Ready;
export const Loading = stories.Loading;
export const Empty = stories.Empty;
export const ErrorState = stories.ErrorState;
export const Denied = stories.Denied;
export const ReadOnly = stories.ReadOnly;
export const Partitioned = stories.Partitioned;
