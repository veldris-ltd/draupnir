import type { JSX } from 'react';
import { cleanup, render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it } from 'vitest';
import { ALL_STATES, REPLACING_STATES, type ComponentState } from './state/states';
import {
  Badge,
  Breadcrumb,
  Button,
  Checkbox,
  Dialog,
  Drawer,
  Input,
  Pagination,
  Radio,
  Select,
  Table,
  Tabs,
  Tag,
  Toast,
  Toggle,
  Tooltip,
} from './primitives';
import {
  CapacityGauge,
  DiffViewer,
  GateCard,
  LedgerEntryViewer,
  LineageTree,
  LogViewer,
  RunCard,
  SweepMatrix,
} from './composites';

/**
 * Component-level behaviour, which is where AC-U2, AC-U6 and AC-U7 are either
 * true or merely claimed.
 *
 * Storybook stories are the visual regression target; these are the
 * behavioural one. A snapshot cannot tell you that a denied table announced
 * itself, that a disabled button said why, or that the log viewer is actually
 * virtualising rather than laying out twenty thousand rows -- and those are
 * the three things most likely to be quietly wrong.
 */

afterEach(cleanup);

type Renderer = (state: ComponentState) => JSX.Element;

const PROBLEM = {
  title: 'The run could not be cancelled',
  detail: 'The run has already reached TRAINED.',
  correlationId: '01a06244-ad82',
};

/**
 * What each state has to say, in words, somewhere in the rendered output.
 *
 * Matching on wording rather than on a data attribute is deliberate: a
 * `data-jg-state` that no human reads is not a state a user can perceive, and
 * AC-U2 is about what the operator is told.
 */
const WORDING_FOR: Record<(typeof REPLACING_STATES)[number], RegExp> = {
  loading: /loading|working|…/i,
  empty: /nothing|none|no options|empty/i,
  error: /error|went wrong|could not|did not|failed/i,
  denied: /not permitted|denied|hidden|does not allow/i,
  partitioned: /partition/i,
};

const COMPONENTS: [name: string, render: Renderer][] = [
  ['Button', (state) => <Button state={state}>Submit run</Button>],
  ['Input', (state) => <Input label="Run name" value="cim-014" state={state} />],
  [
    'Select',
    (state) => (
      <Select label="Tier" options={[{ value: 'a', label: 'Tier A' }]} value="a" state={state} />
    ),
  ],
  ['Checkbox', (state) => <Checkbox label="Publish the card" state={state} />],
  ['Radio', (state) => <Radio name="site" value="edi" label="Edinburgh" state={state} />],
  ['Toggle', (state) => <Toggle label="Continue while partitioned" state={state} />],
  [
    'Table',
    (state) => (
      <Table
        caption="Runs"
        columns={[{ key: 'id', header: 'Run', render: (row: { id: string }) => row.id }]}
        rows={[{ id: '01a06244' }]}
        rowKey={(row) => row.id}
        state={state}
        problem={PROBLEM}
      />
    ),
  ],
  ['Badge', (state) => <Badge state={state}>RELEASED</Badge>],
  ['Tag', (state) => <Tag state={state}>tier-a</Tag>],
  [
    'Tooltip',
    (state) => (
      <Tooltip content="The evidence digest" state={state}>
        <span>Evidence</span>
      </Tooltip>
    ),
  ],
  [
    'Dialog',
    (state) => (
      <Dialog title="Cancel this run?" consequence="The run cannot be resumed." state={state}>
        <p>Run 01a06244.</p>
      </Dialog>
    ),
  ],
  [
    'Drawer',
    (state) => (
      <Drawer title="Run 01a06244" state={state}>
        <p>Submitted at 09:14.</p>
      </Drawer>
    ),
  ],
  ['Toast', (state) => <Toast title="Run submitted" state={state} />],
  [
    'Tabs',
    (state) => (
      <Tabs
        label="Run detail"
        items={[
          { id: 'a', label: 'Overview', content: <p>Overview</p> },
          { id: 'b', label: 'Evidence', content: <p>Evidence</p> },
        ]}
        activeId="a"
        state={state}
      />
    ),
  ],
  ['Breadcrumb', (state) => <Breadcrumb items={[{ label: 'Runs' }]} state={state} />],
  ['Pagination', (state) => <Pagination label="Runs" shown={25} state={state} />],
  [
    'RunCard',
    (state) => (
      <RunCard
        runId="01a06244"
        model="CIM-014 Gaelic"
        runState="TRAINING"
        step={42}
        totalSteps={120}
        state={state}
        problem={PROBLEM}
      />
    ),
  ],
  [
    'GateCard',
    (state) => (
      <GateCard
        gate="release.tier-a"
        decision="deny"
        evidence={[{ kind: 'scan', requirement: 'No criticals', met: false, digest: 'sha256:abc' }]}
        state={state}
        problem={PROBLEM}
      />
    ),
  ],
  [
    'LineageTree',
    (state) => (
      <LineageTree
        label="Lineage"
        roots={[{ id: 'r', kind: 'release', label: 'CIM-014 1.0.0' }]}
        state={state}
        problem={PROBLEM}
      />
    ),
  ],
  [
    'SweepMatrix',
    (state) => (
      <SweepMatrix
        caption="Sweep"
        metrics={[{ key: 'score', label: 'Aggregate', higherIsBetter: true }]}
        arms={[{ id: 'a', label: 'lr 1e-4', values: { score: 0.77 } }]}
        state={state}
        problem={PROBLEM}
      />
    ),
  ],
  [
    'LogViewer',
    (state) => (
      <LogViewer
        label="Training log"
        lines={[{ number: 1, text: 'step 0' }]}
        state={state}
        problem={PROBLEM}
      />
    ),
  ],
  ['CapacityGauge', (state) => <CapacityGauge label="Pool" used={4} total={8} state={state} />],
  [
    'LedgerEntryViewer',
    (state) => (
      <LedgerEntryViewer
        entry={{
          sequence: 1,
          kind: 'run.released',
          recordedAt: '2026-09-02',
          actor: 'a.stewart',
          digest: 'sha256:abc',
          previousDigest: null,
          payload: '{}',
          verified: true,
        }}
        state={state}
        problem={PROBLEM}
      />
    ),
  ],
  [
    'DiffViewer',
    (state) => (
      <DiffViewer
        fromLabel="2026.08.2"
        toLabel="2026.08.3"
        lines={[{ op: 'add', newNumber: 1, text: 'min_score: 0.72' }]}
        state={state}
        problem={PROBLEM}
      />
    ),
  ],
];

describe.each(COMPONENTS)('%s', (name, renderComponent) => {
  it.each(ALL_STATES)('renders in the %s state', (state) => {
    const { container } = render(renderComponent(state));
    expect(container.textContent.trim().length).toBeGreaterThan(0);
  });

  /**
   * AC-U2 and SAD 11F.4 together: a state a component enters silently is a
   * state a screen reader user never learns about. Either the component
   * announces the state in a live region, or -- for a control that stays in
   * place rather than being replaced -- it is inert and carries the reason.
   */
  it.each(REPLACING_STATES)('makes the %s state perceptible', (state) => {
    const { container } = render(renderComponent(state));
    const announced = container.querySelector('[role="status"], [role="alert"]') !== null;
    const explained = WORDING_FOR[state].test(container.textContent);
    expect(
      announced || explained,
      `${name} in ${state} neither announced the state in a live region nor said in words ` +
        `what it means. Rendered: ${container.textContent.slice(0, 160)}`,
    ).toBe(true);
  });

  /**
   * AC-U6: a state that is not `ready` is a state in which nothing can be
   * changed. A control left live under `denied` or `partitioned` submits a
   * request that the server will refuse, having first told the operator it
   * would not.
   */
  it.each(REPLACING_STATES)('disables every acting control in the %s state', (state) => {
    const { container } = render(renderComponent(state));
    const controls = [
      ...container.querySelectorAll<HTMLButtonElement | HTMLInputElement | HTMLSelectElement>(
        'button, input, select',
      ),
    ].filter((control) => control.dataset.jgDismiss !== 'true');
    for (const control of controls) {
      expect(control.disabled, `${name} left a control live in ${state}`).toBe(true);
    }
  });

  it.each(REPLACING_STATES)('keeps its dismissal operable in the %s state', (state) => {
    // The one exception, and it is deliberate: a modal you cannot leave is a
    // worse trap than an action you should not have been offered.
    const { container } = render(renderComponent(state));
    for (const control of container.querySelectorAll<HTMLButtonElement>(
      '[data-jg-dismiss="true"]',
    )) {
      expect(control.disabled, `${name} disabled its dismissal in ${state}`).toBe(false);
    }
  });

  it('keeps its content in the readOnly state', () => {
    // Read-only means "look, do not touch". Replacing the content with a
    // banner would remove the thing being looked at.
    const { container } = render(renderComponent('readOnly'));
    expect(container.querySelector('.jg-state')).toBeNull();
  });
});

describe('Button', () => {
  it.each(REPLACING_STATES)('is disabled and says why in the %s state', (state) => {
    render(<Button state={state}>Submit run</Button>);
    const button = screen.getByRole('button', { name: /submit run/i });
    expect(button).toBeDisabled();
    expect(button.textContent).toMatch(/wait|unavailable|not permitted|read only/i);
  });

  it('is operable in the ready state', async () => {
    let clicked = 0;
    render(
      <Button
        onClick={() => {
          clicked += 1;
        }}
      >
        Submit run
      </Button>,
    );
    await userEvent.click(screen.getByRole('button', { name: /submit run/i }));
    expect(clicked).toBe(1);
  });
});

describe('Toggle', () => {
  it('is a switch, not a checkbox wearing a label', () => {
    render(<Toggle label="Continue while partitioned" checked />);
    const toggle = screen.getByRole('switch', { name: /continue while partitioned/i });
    expect(toggle).toHaveAttribute('aria-checked', 'true');
  });
});

describe('Tabs', () => {
  it('moves between tabs with the arrow keys', async () => {
    const selected: string[] = [];
    render(
      <Tabs
        label="Run detail"
        items={[
          { id: 'a', label: 'Overview', content: <p>Overview</p> },
          { id: 'b', label: 'Evidence', content: <p>Evidence</p> },
        ]}
        activeId="a"
        onSelect={(id) => selected.push(id)}
      />,
    );
    const tabs = screen.getAllByRole('tab');
    tabs[0]?.focus();
    await userEvent.keyboard('{ArrowRight}');
    expect(selected).toContain('b');
  });

  it('exposes one tab as selected', () => {
    render(
      <Tabs
        label="Run detail"
        items={[
          { id: 'a', label: 'Overview', content: <p>Overview</p> },
          { id: 'b', label: 'Evidence', content: <p>Evidence</p> },
        ]}
        activeId="a"
      />,
    );
    expect(
      screen.getAllByRole('tab').filter((tab) => tab.getAttribute('aria-selected') === 'true'),
    ).toHaveLength(1);
  });
});

describe('LineageTree', () => {
  const ROOTS = [
    {
      id: 'release',
      kind: 'release',
      label: 'CIM-014 1.0.0',
      children: [
        { id: 'ckpt', kind: 'checkpoint', label: 'step 120,000' },
        { id: 'eval', kind: 'eval', label: 'RAUN report' },
      ],
    },
  ];

  it('is a tree with levelled items', () => {
    render(<LineageTree label="Lineage" roots={ROOTS} />);
    const tree = screen.getByRole('tree', { name: 'Lineage' });
    expect(within(tree).getAllByRole('treeitem').length).toBe(3);
    expect(within(tree).getAllByRole('treeitem')[0]).toHaveAttribute('aria-level', '1');
  });

  it('collapses and expands with the arrow keys', async () => {
    render(<LineageTree label="Lineage" roots={ROOTS} />);
    const root = screen.getAllByRole('treeitem')[0];
    expect(root).toHaveAttribute('aria-expanded', 'true');
    screen.getByRole('button', { name: /CIM-014 1\.0\.0/ }).focus();
    await userEvent.keyboard('{ArrowLeft}');
    expect(screen.getAllByRole('treeitem')[0]).toHaveAttribute('aria-expanded', 'false');
    await userEvent.keyboard('{ArrowRight}');
    expect(screen.getAllByRole('treeitem')[0]).toHaveAttribute('aria-expanded', 'true');
  });

  it('keeps exactly one item in the tab order', () => {
    render(<LineageTree label="Lineage" roots={ROOTS} />);
    const focusable = screen
      .getAllByRole('button')
      .filter((button) => button.getAttribute('tabindex') === '0');
    expect(focusable).toHaveLength(1);
  });
});

describe('CapacityGauge', () => {
  it('is a meter, not a progress bar', () => {
    // A progress bar is a task advancing to completion. A pool at 100% has
    // not finished anything.
    render(<CapacityGauge label="Edinburgh pool" used={432} total={512} unit="GPUs" />);
    const meter = screen.getByRole('meter', { name: 'Edinburgh pool' });
    expect(meter).toHaveAttribute('aria-valuenow', '432');
    expect(meter).toHaveAttribute('aria-valuemax', '512');
    expect(meter.getAttribute('aria-valuetext')).toMatch(/432 of 512 GPUs in use/);
  });

  it('names the band in words as well as colour', () => {
    render(<CapacityGauge label="Pool" used={512} total={512} />);
    expect(screen.getByText(/exhausted/i)).toBeInTheDocument();
  });
});

describe('LogViewer', () => {
  it('renders a window rather than every line', () => {
    const lines = Array.from({ length: 20_000 }, (_, index) => ({
      number: index + 1,
      text: `step ${String(index)}`,
    }));
    const { container } = render(<LogViewer label="Training log" lines={lines} />);
    const rendered = container.querySelectorAll('.jg-log__line').length;
    expect(rendered).toBeGreaterThan(0);
    expect(rendered).toBeLessThan(200);
  });

  it('states the total and the visible range for a screen reader', () => {
    const lines = Array.from({ length: 20_000 }, (_, index) => ({
      number: index + 1,
      text: `step ${String(index)}`,
    }));
    render(<LogViewer label="Training log" lines={lines} />);
    expect(screen.getByRole('log').getAttribute('aria-label')).toMatch(
      /20,000 lines, showing 1 to/,
    );
  });
});

describe('DiffViewer', () => {
  it('does not rely on colour to distinguish added from removed', () => {
    const { container } = render(
      <DiffViewer
        fromLabel="a"
        toLabel="b"
        lines={[
          { op: 'remove', oldNumber: 1, text: 'min_score: 0.70' },
          { op: 'add', newNumber: 1, text: 'min_score: 0.72' },
        ]}
      />,
    );
    expect(container.querySelectorAll('.jg-diff__sign')).toHaveLength(2);
    expect(screen.getByText(/Added:/)).toBeInTheDocument();
    expect(screen.getByText(/Removed:/)).toBeInTheDocument();
  });
});

describe('SweepMatrix', () => {
  it('marks the best cell in text as well as colour', () => {
    render(
      <SweepMatrix
        caption="Sweep"
        metrics={[{ key: 'score', label: 'Aggregate', higherIsBetter: true }]}
        arms={[
          { id: 'a', label: 'lr 1e-4', values: { score: 0.77 } },
          { id: 'b', label: 'lr 3e-4', values: { score: 0.78 } },
        ]}
      />,
    );
    expect(screen.getAllByText(/Best:/)).toHaveLength(1);
  });

  it('marks every cell of a tie', () => {
    render(
      <SweepMatrix
        caption="Sweep"
        metrics={[{ key: 'score', label: 'Aggregate', higherIsBetter: true }]}
        arms={[
          { id: 'a', label: 'lr 1e-4', values: { score: 0.78 } },
          { id: 'b', label: 'lr 3e-4', values: { score: 0.78 } },
        ]}
      />,
    );
    expect(screen.getAllByText(/Best:/)).toHaveLength(2);
  });

  it('says when a metric was not measured', () => {
    render(
      <SweepMatrix
        caption="Sweep"
        metrics={[{ key: 'hours', label: 'GPU time', higherIsBetter: false }]}
        arms={[{ id: 'a', label: 'lr 1e-4', values: {} }]}
      />,
    );
    // Visually hidden text rather than `aria-label`: a bare `span` has no
    // role that supports an accessible name, and axe refuses it.
    expect(screen.getByText('Not measured')).toBeInTheDocument();
  });
});

describe('GateCard', () => {
  it('puts the evidence digest on the face of the card', () => {
    render(
      <GateCard
        gate="release.tier-a"
        decision="deny"
        evidence={[
          { kind: 'scan', requirement: 'No criticals', met: false, digest: 'sha256:deadbeef' },
        ]}
      />,
    );
    expect(screen.getByText('sha256:deadbeef')).toBeInTheDocument();
    expect(screen.getByText(/Not met:/)).toBeInTheDocument();
  });

  it('refuses to present an unattributed waiver as a clean one', () => {
    // A waiver with no named approver is the custody failure of SAD 16A. The
    // card says so rather than rendering a blank field.
    render(<GateCard gate="release.tier-a" decision="waived" evidence={[]} />);
    expect(screen.getAllByText(/Unrecorded — this is a defect/)).toHaveLength(2);
  });
});

describe('LedgerEntryViewer', () => {
  it('shows a broken chain rather than hiding the entry', () => {
    render(
      <LedgerEntryViewer
        entry={{
          sequence: 4812,
          kind: 'run.released',
          recordedAt: '2026-09-02',
          actor: 'a.stewart',
          digest: 'sha256:abc',
          previousDigest: 'sha256:xyz',
          payload: '{}',
          verified: false,
        }}
      />,
    );
    expect(screen.getByText(/did not verify/i)).toBeInTheDocument();
    expect(screen.getByText('sha256:abc')).toBeInTheDocument();
  });
});

describe('Dialog', () => {
  it('states the consequence rather than asking whether you are sure', () => {
    render(
      <Dialog title="Cancel this run?" consequence="The run cannot be resumed." state="ready">
        <p>Run 01a06244.</p>
      </Dialog>,
    );
    expect(screen.getByText('The run cannot be resumed.')).toBeInTheDocument();
  });
});
