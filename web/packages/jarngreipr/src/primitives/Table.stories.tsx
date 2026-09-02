import type { Meta } from '@storybook/react';
import { COMPONENT_META, SAMPLE_PROBLEM, stateStories } from '../state/stories';
import { Table } from '.';

// A literal, because Storybook's story indexer is static: a default export it
// cannot read at parse time is a component that never appears in the sidebar.
export default {
  title: 'Primitives/Table',
  ...COMPONENT_META,
} satisfies Meta;

interface Row {
  id: string;
  model: string;
  step: number;
}

const COLUMNS = [
  { key: 'id', header: 'Run', render: (row: Row) => row.id },
  { key: 'model', header: 'Model', render: (row: Row) => row.model },
  {
    key: 'step',
    header: 'Step',
    numeric: true,
    render: (row: Row) => row.step.toLocaleString(),
  },
];

const ROWS: Row[] = [
  { id: '01a06244-ad82', model: 'CIM-014 Gaelic', step: 42_000 },
  { id: '01a06244-b103', model: 'CIM-031 Maltese', step: 11_500 },
];

const stories = stateStories((state) => (
  <Table
    caption="Runs in the last hour"
    columns={COLUMNS}
    rows={ROWS}
    rowKey={(row) => row.id}
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
