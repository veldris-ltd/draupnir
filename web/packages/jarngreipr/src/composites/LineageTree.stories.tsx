import type { Meta } from '@storybook/react';
import { COMPONENT_META, SAMPLE_PROBLEM, stateStories } from '../state/stories';
import { LineageTree } from '.';

// A literal, because Storybook's story indexer is static: a default export it
// cannot read at parse time is a component that never appears in the sidebar.
export default {
  title: 'Composites/Lineage tree',
  ...COMPONENT_META,
} satisfies Meta;

const ROOTS = [
  {
    id: 'release-014',
    kind: 'release',
    label: 'CIM-014 Gaelic 1.0.0',
    digest: 'sha256:9f2c1b7e4a0d',
    children: [
      {
        id: 'ckpt-120000',
        kind: 'checkpoint',
        label: 'step 120,000',
        digest: 'sha256:31d8a7c05e6b',
        children: [
          {
            id: 'corpus-ga',
            kind: 'corpus',
            label: 'Gaelic corpus v4',
            digest: 'sha256:5b0e93d21fa7',
          },
          {
            id: 'base-cim0',
            kind: 'checkpoint',
            label: 'CIM-000 base',
            digest: 'sha256:a71c4e8b0d92',
          },
        ],
      },
      { id: 'eval-014', kind: 'eval', label: 'RAUN report', digest: 'sha256:c40b17ea9d35' },
    ],
  },
];

const stories = stateStories((state) => (
  <LineageTree
    label="Lineage of CIM-014 Gaelic"
    roots={ROOTS}
    selectedId="ckpt-120000"
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
