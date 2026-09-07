import type { JSX } from 'react';
import { useState } from 'react';
import { cleanup, fireEvent, render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it } from 'vitest';
import { ALL_STATES, REPLACING_STATES, type ComponentState } from './state/states';
import {
  Badge,
  Breadcrumb,
  Button,
  Checkbox,
  Combobox,
  Dialog,
  Drawer,
  Input,
  Pagination,
  Pill,
  Radio,
  Select,
  Table,
  Tabs,
  Tag,
  TextArea,
  Toast,
  Toggle,
  Tooltip,
  wantsCombobox,
} from './primitives';
import {
  CapacityGauge,
  Digest,
  DiffViewer,
  ESTIMATE_MINIMUM_SAMPLES,
  EvidencePanel,
  GAP_KIND,
  GateCard,
  LedgerEntryViewer,
  LineageTree,
  LogViewer,
  RunCard,
  SpecEditor,
  SweepMatrix,
  countGaps,
  estimateRemaining,
  tradeSentence,
  withGaps,
  type EvidenceRow,
  type LineageNode,
} from './composites';

/** A measurement, for every gate card the state table renders. */
const MEASUREMENT = { value: 0.8123, baseline: 0.8041, margin: 0.0082 };

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
  ['TextArea', (state) => <TextArea label="Requeue reason" state={state} />],
  [
    'Combobox',
    (state) => (
      <Combobox
        label="Jurisdiction"
        options={[{ value: 'gbr', label: 'United Kingdom' }]}
        state={state}
      />
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
  ['Pill', (state) => <Pill runState="TRAINING" state={state} />],
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
        measurement={MEASUREMENT}
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
    'Digest',
    (state) => <Digest value={'a'.repeat(64)} of="adapter" state={state} problem={PROBLEM} />,
  ],
  [
    'EvidencePanel',
    (state) => (
      <EvidencePanel
        suiteVersion="general-core/2026.01"
        rows={[
          {
            gate: 'E1',
            statement: 'general capability has not regressed',
            passed: true,
            measurement: MEASUREMENT,
          },
        ]}
        state={state}
        problem={PROBLEM}
      />
    ),
  ],
  [
    'SpecEditor',
    (state) => (
      <SpecEditor
        label="Run specification"
        value='{"kind":"AdapterRun"}'
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

/** One lineage row by its label. The row is the treeitem, not a button in it. */
function row(container: HTMLElement, label: RegExp): HTMLElement {
  const found = [...container.querySelectorAll<HTMLElement>('.jg-tree__row')].find((node) =>
    label.test(node.textContent),
  );
  if (found === undefined) throw new Error(`no lineage row matching ${String(label)}`);
  return found;
}

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
    const { container } = render(<LineageTree label="Lineage" roots={ROOTS} />);
    const root = screen.getAllByRole('treeitem')[0];
    expect(root).toHaveAttribute('aria-expanded', 'true');
    // The row is the treeitem itself, not a button inside it: a button there
    // would make every digest's copy control a control inside a control.
    row(container, /CIM-014 1\.0\.0/).focus();
    await userEvent.keyboard('{ArrowLeft}');
    expect(screen.getAllByRole('treeitem')[0]).toHaveAttribute('aria-expanded', 'false');
    await userEvent.keyboard('{ArrowRight}');
    expect(screen.getAllByRole('treeitem')[0]).toHaveAttribute('aria-expanded', 'true');
  });

  it('keeps exactly one item in the tab order', () => {
    const { container } = render(<LineageTree label="Lineage" roots={ROOTS} />);
    const focusable = [...container.querySelectorAll('.jg-tree__row')].filter(
      (node) => node.getAttribute('tabindex') === '0',
    );
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
    const { container } = render(
      <GateCard
        gate="release.tier-a"
        decision="deny"
        measurement={MEASUREMENT}
        evidence={[
          { kind: 'scan', requirement: 'No criticals', met: false, digest: 'sha256:deadbeef' },
        ]}
      />,
    );
    // Truncated on the face, whole in the accessibility tree. AC-V8.
    expect(container.querySelector('.jg-digest__value')?.textContent).toBe('sha256:d…');
    expect(screen.getByText(/full value sha256:deadbeef/)).toBeInTheDocument();
    expect(screen.getByText(/Not met:/)).toBeInTheDocument();
  });

  it('refuses to present an unattributed waiver as a clean one', () => {
    // A waiver with no named approver is the custody failure of SAD 16A. The
    // card says so rather than rendering a blank field.
    render(
      <GateCard gate="release.tier-a" decision="waived" measurement={MEASUREMENT} evidence={[]} />,
    );
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

// ---------------------------------------------------------------------------
// Section 5.1's notes, which are requirements
// ---------------------------------------------------------------------------

describe('every field', () => {
  /**
   * Section 5.1: "label always visible. Placeholder never substitutes for a
   * label." Both halves are checked, because the second is the one a rushed
   * screen breaks: a placeholder disappears when somebody types, so a field
   * labelled by one leaves the user unable to check their own answer, and most
   * screen readers do not announce it at all.
   */
  const FIELDS: [name: string, element: JSX.Element][] = [
    ['Input', <Input key="i" label="Run name" placeholder="cim-gbr-v1.0" />],
    ['TextArea', <TextArea key="t" label="Requeue reason" placeholder="E1 margin" />],
    [
      'Select',
      <Select key="s" label="Tier" options={[{ value: 'a', label: 'Tier A' }]} value="a" />,
    ],
    [
      'Combobox',
      <Combobox
        key="c"
        label="Jurisdiction"
        placeholder="Type to filter"
        options={[{ value: 'gbr', label: 'United Kingdom' }]}
      />,
    ],
  ];

  it.each(FIELDS)('%s renders a visible label bound to its control', (name, element) => {
    const { container } = render(element);
    const control = container.querySelector('input, textarea, select');
    expect(control, `${name} rendered no control`).not.toBeNull();

    const label = container.querySelector('label');
    expect(label, `${name} rendered no visible label`).not.toBeNull();
    // Visible, not merely present. A label in `.jg-sr-only` would pass an
    // accessible-name check and fail the requirement.
    expect(label?.className ?? '', `${name} hid its label`).not.toContain('sr-only');
    expect(label?.getAttribute('for')).toBe(control?.getAttribute('id'));
    expect((label?.textContent ?? '').trim().length).toBeGreaterThan(0);
  });

  it.each(FIELDS)('%s keeps the label when the placeholder is gone', (name, element) => {
    const { container } = render(element);
    const control = container.querySelector<HTMLInputElement>('input, textarea, select');
    const placeholder = control?.getAttribute('placeholder') ?? '';
    const label = container.querySelector('label')?.textContent ?? '';
    // The two say different things. A placeholder that repeats the label is
    // the cosmetic version of the same mistake.
    if (placeholder !== '') {
      expect(placeholder.toLowerCase(), `${name} used its label as a placeholder`).not.toBe(
        label.trim().toLowerCase(),
      );
    }
  });
});

describe('Combobox', () => {
  const OPTIONS = ['Australia', 'Canada', 'Ghana', 'Kenya', 'United Kingdom'].map((label) => ({
    value: label.toLowerCase(),
    label,
  }));

  it('is the ARIA combobox pattern, not a text box beside a list', () => {
    render(<Combobox label="Jurisdiction" options={OPTIONS} />);
    const input = screen.getByRole('combobox', { name: /jurisdiction/i });
    expect(input).toHaveAttribute('aria-expanded', 'false');
    expect(input).toHaveAttribute('aria-controls');
    expect(input).toHaveAttribute('aria-autocomplete', 'list');
  });

  it('filters as you type and keeps focus in the input', async () => {
    const user = userEvent.setup();
    render(<Combobox label="Jurisdiction" options={OPTIONS} />);
    const input = screen.getByRole('combobox', { name: /jurisdiction/i });

    await user.click(input);
    await user.keyboard('ken');

    const list = screen.getByRole('listbox', { name: /jurisdiction/i });
    expect(within(list).getAllByRole('option')).toHaveLength(1);
    expect(within(list).getByRole('option', { name: 'Kenya' })).toBeInTheDocument();
    // Focus never leaves the input; the arrow keys move `aria-activedescendant`
    // instead. That is what lets somebody keep typing to narrow the list.
    expect(document.activeElement).toBe(input);
  });

  it('chooses with the keyboard alone', async () => {
    const chosen: string[] = [];
    const user = userEvent.setup();
    render(
      <Combobox
        label="Jurisdiction"
        options={OPTIONS}
        onChange={(value) => {
          chosen.push(value);
        }}
      />,
    );

    const input = screen.getByRole('combobox', { name: /jurisdiction/i });
    await user.click(input);
    await user.keyboard('{ArrowDown}{ArrowDown}{Enter}');

    expect(chosen).toEqual(['canada']);
  });

  it('says what would be here when the filter matches nothing', async () => {
    const user = userEvent.setup();
    render(
      <Combobox
        label="Jurisdiction"
        options={OPTIONS}
        emptyMessage="No jurisdiction matches that."
      />,
    );
    await user.click(screen.getByRole('combobox', { name: /jurisdiction/i }));
    await user.keyboard('zzz');
    expect(screen.getByText(/no jurisdiction matches that/i)).toBeInTheDocument();
  });

  it('knows when an option set is long enough to need one', () => {
    // Section 5.1: native select below twelve options, combobox above.
    expect(wantsCombobox(new Array(12).fill(0))).toBe(false);
    expect(wantsCombobox(new Array(13).fill(0))).toBe(true);
  });
});

describe('Dialog focus (AC-X4)', () => {
  function Harness({ destructive }: { destructive: boolean }): JSX.Element {
    const [open, setOpen] = useState(false);
    return (
      <>
        <button
          type="button"
          onClick={() => {
            setOpen(true);
          }}
        >
          Open
        </button>
        {open ? (
          <Dialog
            title="Cancel this run?"
            {...(destructive ? { consequence: 'The run cannot be resumed.' } : {})}
            onDismiss={() => {
              setOpen(false);
            }}
          >
            <p>Run 01a06244.</p>
          </Dialog>
        ) : null}
      </>
    );
  }

  it('moves focus into the dialog when it opens', async () => {
    const user = userEvent.setup();
    render(<Harness destructive={false} />);
    await user.click(screen.getByRole('button', { name: 'Open' }));

    const dialog = screen.getByRole('dialog');
    expect(dialog.contains(document.activeElement)).toBe(true);
  });

  it('wraps at the ends rather than escaping to the page behind', async () => {
    const user = userEvent.setup();
    render(<Harness destructive={false} />);
    await user.click(screen.getByRole('button', { name: 'Open' }));

    const dialog = screen.getByRole('dialog');
    // Enough presses to walk past the end of any dialog this size. Focus is
    // still inside: that is the whole of what a trap means.
    for (let press = 0; press < 8; press += 1) {
      await user.tab();
      expect(dialog.contains(document.activeElement)).toBe(true);
    }
  });

  it('restores focus to the control that opened it', async () => {
    const user = userEvent.setup();
    render(<Harness destructive={false} />);
    const opener = screen.getByRole('button', { name: 'Open' });

    await user.click(opener);
    await user.click(screen.getByRole('button', { name: /cancel/i }));

    expect(document.activeElement).toBe(opener);
  });

  it('closes on escape, and does not when the action is destructive', async () => {
    const user = userEvent.setup();
    const { unmount } = render(<Harness destructive={false} />);
    await user.click(screen.getByRole('button', { name: 'Open' }));
    await user.keyboard('{Escape}');
    expect(screen.queryByRole('dialog')).toBeNull();
    unmount();

    render(<Harness destructive />);
    await user.click(screen.getByRole('button', { name: 'Open' }));
    await user.keyboard('{Escape}');
    // Still there. A key pressed by accident must not dismiss the surface that
    // exists to slow somebody down; cancel still closes it, and it says so.
    expect(screen.getByRole('dialog')).toBeInTheDocument();
    expect(screen.getByText(/escape does not close this dialog/i)).toBeInTheDocument();
  });
});

describe('Table keyboard (section 5.1)', () => {
  const ROWS = [
    { id: '01a06244', node: 'dvalin' },
    { id: '01a06245', node: 'durin' },
    { id: '01a06246', node: 'dain' },
  ];

  function grid(): JSX.Element {
    return (
      <Table
        caption="Runs"
        columns={[
          { key: 'id', header: 'Run', render: (row: (typeof ROWS)[number]) => row.id },
          {
            key: 'node',
            header: 'Appliance',
            render: (row: (typeof ROWS)[number]) => row.node,
            sortKey: (row: (typeof ROWS)[number]) => row.node,
          },
        ]}
        rows={ROWS}
        rowKey={(row) => row.id}
      />
    );
  }

  it('gives every header cell a scope', () => {
    render(grid());
    for (const header of screen.getAllByRole('columnheader')) {
      expect(header).toHaveAttribute('scope', 'col');
    }
  });

  it('is one tab stop, and the arrow keys move within it', async () => {
    const user = userEvent.setup();
    render(grid());

    const rows = screen.getAllByRole('row').slice(1);
    // A roving tabindex: exactly one row is reachable by Tab, so the table
    // does not cost a keyboard user one press per row.
    expect(rows.filter((row) => row.getAttribute('tabindex') === '0')).toHaveLength(1);

    rows[0]?.focus();
    await user.keyboard('{ArrowDown}');
    expect(document.activeElement).toBe(rows[1]);
    await user.keyboard('{End}');
    expect(document.activeElement).toBe(rows[2]);
    await user.keyboard('{Home}');
    expect(document.activeElement).toBe(rows[0]);
  });

  it('jumps to a row by typing', async () => {
    const user = userEvent.setup();
    render(grid());
    const rows = screen.getAllByRole('row').slice(1);

    rows[0]?.focus();
    await user.keyboard('du');
    expect(document.activeElement).toBe(rows[1]);
  });

  it('announces its sort on the header, not only in a glyph', async () => {
    const user = userEvent.setup();
    render(grid());

    const header = screen.getByRole('columnheader', { name: /appliance/i });
    expect(header).toHaveAttribute('aria-sort', 'none');

    await user.click(within(header).getByRole('button'));
    expect(header).toHaveAttribute('aria-sort', 'ascending');
    // Sorted, not merely marked.
    expect(screen.getAllByRole('row')[1]?.textContent).toContain('dain');

    await user.click(within(header).getByRole('button'));
    expect(header).toHaveAttribute('aria-sort', 'descending');
  });
});

describe('Toast (section 5.1)', () => {
  it('is a polite status, never an alert', () => {
    // "Transient confirmations only. Never an error that requires action." A
    // toast that disappears is the worst carrier for something a user has to
    // do, so there is nothing assertive in here by construction. The `danger`
    // tone is excluded in the type, which is why this only has to check what
    // is rendered.
    const { container } = render(<Toast title="Run submitted" />);
    const toast = container.querySelector('.jg-toast');
    expect(toast).toHaveAttribute('role', 'status');
    expect(toast).toHaveAttribute('aria-live', 'polite');
  });

  it('carries no action beyond dismissing itself', () => {
    render(<Toast title="Run submitted" detail="01a06244 is queued." />);
    const buttons = screen.getAllByRole('button');
    expect(buttons).toHaveLength(1);
    expect(buttons[0]).toHaveAccessibleName(/dismiss/i);
  });
});

describe('Tooltip (section 5.1)', () => {
  it('is never the sole carrier of what it says', () => {
    // Closed. The text is still in the accessibility tree, so a screen reader
    // user and a touch user -- neither of whom can hover -- get it anyway.
    const { container } = render(
      <Tooltip content="The digest the gate was measured on">
        <span>Evidence</span>
      </Tooltip>,
    );
    expect(container.textContent).toContain('The digest the gate was measured on');
  });

  it('describes its trigger rather than naming it', () => {
    render(
      <Tooltip content="The digest the gate was measured on">
        <button type="button">Evidence</button>
      </Tooltip>,
    );
    // A tooltip that replaced the accessible name would leave the control
    // unnamed the moment the tooltip closed.
    expect(screen.getByRole('button')).toHaveAccessibleName('Evidence');
  });
});

describe('Pagination (section 5.1)', () => {
  it('is cursor based, with no page numbers', () => {
    render(<Pagination label="Runs" shown={25} nextCursor="abc" hasPrevious />);
    const names = screen.getAllByRole('button').map((button) => button.textContent);
    expect(names.some((name) => /^\s*\d+\s*$/.test(name))).toBe(false);
    expect(names.join(' ')).toMatch(/previous/i);
    expect(names.join(' ')).toMatch(/next/i);
  });
});

describe('icon-bearing controls (AC-V10)', () => {
  it('an icon-only button takes its name from its children', () => {
    render(
      <Button iconOnly icon={<svg aria-hidden="true" />}>
        Copy the run identifier
      </Button>,
    );
    // The glyph is decoration; the name is the sentence. `iconOnly` without a
    // string child does not compile, which is where the rule is really kept.
    expect(screen.getByRole('button')).toHaveAccessibleName('Copy the run identifier');
  });

  it('an icon beside a label is hidden from the accessibility tree', () => {
    const { container } = render(<Button icon={<svg data-testid="glyph" />}>Retry</Button>);
    expect(container.querySelector('.jg-button__icon')).toHaveAttribute('aria-hidden', 'true');
    expect(screen.getByRole('button')).toHaveAccessibleName('Retry');
  });
});

describe('Pill (AC-V4, AC-V5)', () => {
  it('names the state in text as well as in colour', () => {
    render(<Pill runState="AWAITING_APPROVAL" />);
    expect(screen.getByText(/awaiting approval/i)).toBeInTheDocument();
  });

  it('takes its colour from the token layer rather than deciding one', () => {
    const { container } = render(<Pill runState="TRAINING" />);
    const pill = container.querySelector('.jg-state-pill');
    expect(pill).toHaveAttribute('data-jg-run-state', 'TRAINING');
    // No inline style: the attribute is the whole of what the component says
    // about appearance, and `state.css` says the rest.
    expect(pill?.getAttribute('style')).toBeNull();
  });
});

// ---------------------------------------------------------------------------
// Section 5.2's notes, which are requirements
// ---------------------------------------------------------------------------

describe('RunCard estimate (section 5.2)', () => {
  it('omits the estimate rather than guessing from too few steps', () => {
    const { because, seconds } = estimateRemaining([40, 41], 2, 1000);
    expect(seconds).toBeNull();
    expect(because).toContain('not enough steps');
  });

  it('omits it when step time is not stable', () => {
    // Same mean, wildly different steps: a run that has just moved to a
    // contended appliance has samples and none of them predict the next one.
    const { seconds, because } = estimateRemaining([10, 90, 12, 88, 11], 5, 100);
    expect(seconds).toBeNull();
    expect(because).toContain('not stable');
  });

  it('gives one when the step time is steady', () => {
    const { seconds } = estimateRemaining([40, 41, 40, 39, 40], 100, 200);
    expect(seconds).toBeCloseTo(4000, -2);
  });

  it('says on the card that it is not estimated, rather than dropping the row', () => {
    render(
      <RunCard
        runId="01a06244"
        model="cim-gbr-v1.0"
        runState="TRAINING"
        step={2}
        totalSteps={1000}
        stepSeconds={[40, 41]}
        node="dvalin"
        elapsedSeconds={5400}
      />,
    );
    // The row is there and says why. A missing row reads as a card that forgot.
    expect(screen.getByText(/not estimated/i)).toBeInTheDocument();
    expect(screen.getByText(/dvalin/)).toBeInTheDocument();
    expect(screen.getByText('1h 30m')).toBeInTheDocument();
  });

  it('needs five samples, as the constant says', () => {
    expect(ESTIMATE_MINIMUM_SAMPLES).toBe(5);
  });
});

describe('GateCard measurement (section 5.2)', () => {
  it('renders value, baseline and margin, never a bare tick', () => {
    render(
      <GateCard
        gate="E1"
        decision="allow"
        measurement={{ value: 0.8123, baseline: 0.8041, margin: 0.0082 }}
        evidence={[]}
      />,
    );
    expect(screen.getByText('0.8123')).toBeInTheDocument();
    expect(screen.getByText('0.8041')).toBeInTheDocument();
    // Signed, because +0.0082 and -0.0082 are opposite answers.
    expect(screen.getByText('+0.0082')).toBeInTheDocument();
  });

  it('marks a negative margin with its sign, not only a colour', () => {
    render(
      <GateCard
        gate="E5"
        decision="deny"
        measurement={{ value: 0.9, baseline: 0.91, margin: -0.01 }}
        evidence={[]}
      />,
    );
    expect(screen.getByText('−0.01')).toBeInTheDocument();
  });
});

describe('EvidencePanel (section 5.2, AC-S8)', () => {
  const ROWS: EvidenceRow[] = [
    {
      gate: 'E1',
      statement: 'general capability',
      passed: true,
      measurement: { value: 0.81, baseline: 0.8, margin: 0.01 },
    },
    {
      gate: 'E5',
      statement: 'refusal behaviour',
      passed: true,
      measurement: { value: 0.9107, baseline: 0.9098, margin: 0.0009 },
    },
    {
      gate: 'E2',
      statement: 'jurisdiction capability',
      passed: true,
      measurement: { value: 0.77, baseline: 0.73, margin: 0.04 },
    },
  ];

  it('sorts by margin so the tightest result is first', () => {
    render(<EvidencePanel suiteVersion="general-core/2026.01" rows={ROWS} />);
    const rows = screen.getAllByRole('row').slice(1);
    expect(rows[0]?.textContent).toContain('E5');
  });

  it('states the tightest result in a sentence as well', () => {
    render(<EvidencePanel suiteVersion="general-core/2026.01" rows={ROWS} />);
    expect(screen.getByText(/tightest result: E5 passed by \+0.0009/i)).toBeInTheDocument();
  });

  it('renders a result as a word, never as a bare glyph', () => {
    render(<EvidencePanel suiteVersion="general-core/2026.01" rows={ROWS} />);
    expect(screen.getAllByText('Passed').length).toBe(ROWS.length);
  });
});

describe('LineageTree gaps (AC-S11)', () => {
  /**
   * The proof the prompt asks for: a gap cannot be rendered by omission.
   *
   * The tree does not take a caller's word for its own completeness. Each node
   * states what it derives from, the tree checks those identifiers against
   * what is actually present, and anything claimed and absent becomes a marked
   * node. So a caller supplying a lineage with a hole gets a tree that is
   * *longer*, never shorter -- which is what "never as a shorter tree" means.
   */
  const BROKEN: LineageNode[] = [
    {
      id: 'release-1',
      kind: 'release',
      label: 'cim-gbr-v1.0',
      derivedFrom: ['adapter-1'],
      children: [
        {
          id: 'adapter-1',
          kind: 'checkpoint',
          label: 'adapter',
          // The corpus this adapter was trained on is not in the tree.
          derivedFrom: ['corpus-1'],
        },
      ],
    },
  ];

  it('inserts a marked node for an ancestor that is claimed and absent', () => {
    const complete = withGaps(BROKEN);
    expect(countGaps(complete)).toBe(1);

    render(<LineageTree label="Lineage" roots={BROKEN} />);
    expect(screen.getByText(/missing: corpus-1/i)).toBeInTheDocument();
  });

  it('cannot be made to omit a gap by leaving the ancestor out', () => {
    // The whole claim, stated as a property: for every lineage with a claimed
    // ancestor that is absent, the rendered tree has at least one gap node.
    // There is no input that produces a hole and a clean tree, because the
    // component derives the gaps rather than being told about them.
    const holes: LineageNode[][] = [
      BROKEN,
      [{ id: 'a', kind: 'release', label: 'a', derivedFrom: ['ghost'] }],
      [
        {
          id: 'a',
          kind: 'release',
          label: 'a',
          children: [
            { id: 'b', kind: 'eval', label: 'b', derivedFrom: ['missing-1', 'missing-2'] },
          ],
        },
      ],
    ];

    for (const roots of holes) {
      const complete = withGaps(roots);
      expect(countGaps(complete)).toBeGreaterThan(0);
      // And the tree grew rather than shrank: a gap is a node, not an absence.
      expect(countNodes(complete)).toBeGreaterThan(countNodes(roots));
    }
  });

  it('leaves a complete lineage alone', () => {
    const whole: LineageNode[] = [
      {
        id: 'release-1',
        kind: 'release',
        label: 'cim-gbr-v1.0',
        derivedFrom: ['adapter-1'],
        children: [{ id: 'adapter-1', kind: 'checkpoint', label: 'adapter', derivedFrom: [] }],
      },
    ];
    expect(countGaps(withGaps(whole))).toBe(0);
    render(<LineageTree label="Lineage" roots={whole} />);
    expect(screen.queryByText(/missing:/i)).toBeNull();
  });

  it('says how many gaps there are above the tree, not only within it', () => {
    // A gap six levels down in a collapsed branch is a gap somebody has to go
    // looking for, and the point of AC-S11 is that they should not have to.
    render(<LineageTree label="Lineage" roots={BROKEN} />);
    expect(screen.getByText(/one ancestor of this lineage is missing/i)).toBeInTheDocument();
  });

  it('reserves its kind, so a real artefact cannot pose as a gap', () => {
    expect(GAP_KIND).toBe('gap');
  });
});

function countNodes(nodes: LineageNode[]): number {
  return nodes.reduce((total, node) => total + 1 + countNodes([...(node.children ?? [])]), 0);
}

describe('SweepMatrix selection (AC-S12)', () => {
  const METRICS = [
    { key: 'e1', label: 'E1', higherIsBetter: true },
    { key: 'e2', label: 'E2', higherIsBetter: true },
  ];
  const ARMS = [
    { id: 'w2', label: 'weight 0.2', values: { e1: 0.8, e2: 0.7 } },
    { id: 'w6', label: 'weight 0.6', values: { e1: 0.79, e2: 0.78 } },
    { id: 'w10', label: 'weight 1.0', values: { e1: 0.75, e2: 0.8 } },
  ];

  it('marks the selected point in text as well as in colour', () => {
    render(<SweepMatrix caption="Sweep" metrics={METRICS} arms={ARMS} selectedId="w6" />);
    const row = screen.getByRole('rowheader', { name: /weight 0.6/ });
    expect(row.textContent).toContain('Selected');
  });

  it('states the trade in a sentence generated from the numbers', () => {
    render(<SweepMatrix caption="Sweep" metrics={METRICS} arms={ARMS} selectedId="w6" />);
    // 0.79 against a best of 0.8 on E1, and 0.78 against a best of 0.8 on E2.
    const sentence = screen.getByText(/weight 0.6 gives up/i);
    expect(sentence.textContent).toContain('0.01 on E1');
    expect(sentence.textContent).toContain('0.02 on E2');
  });

  it('reports a gain and a loss on the same point', () => {
    // w2 is the best on E1 by 0.01 and the worst on E2 by 0.1. A sentence that
    // reported only one of those would be the hard-coded kind.
    expect(tradeSentence(METRICS, ARMS, 'w2')).toBe(
      'weight 0.2 gives up 0.1 on E2 to gain 0.01 on E1.',
    );
  });

  it('says so when the selected point leads on everything', () => {
    const ahead = [
      { id: 'best', label: 'weight 0.4', values: { e1: 0.9, e2: 0.9 } },
      { id: 'other', label: 'weight 0.8', values: { e1: 0.8, e2: 0.8 } },
    ];
    expect(tradeSentence(METRICS, ahead, 'best')).toMatch(
      /is the best point measured, leading by 0.1 on E1 and 0.1 on E2/,
    );
  });

  it('says nothing when nothing is selected', () => {
    expect(tradeSentence(METRICS, ARMS, undefined)).toBe('');
  });
});

/**
 * Put the log viewport where somebody who has scrolled back to read is.
 *
 * jsdom has no layout, so the geometry is stated rather than measured. Shared
 * because two tests need the same position and a copy of it in each is a copy
 * that drifts.
 */
function scrolledBack(container: HTMLElement): HTMLElement {
  const viewport = container.querySelector<HTMLElement>('.jg-log__viewport');
  if (viewport === null) throw new Error('the log viewer rendered no viewport');
  Object.defineProperty(viewport, 'scrollHeight', { value: 10_000, configurable: true });
  Object.defineProperty(viewport, 'clientHeight', { value: 320, configurable: true });
  Object.defineProperty(viewport, 'scrollTop', { value: 100, configurable: true });
  fireEvent.scroll(viewport);
  return viewport;
}

describe('LogViewer follow (AC-X8)', () => {
  const LINES = Array.from({ length: 500 }, (_, index) => ({
    number: index + 1,
    text: `step ${String(index + 1)}`,
  }));

  it('follows the tail by default and states that it does', () => {
    const { container } = render(<LogViewer label="Training log" lines={LINES} streaming />);
    const status = container.querySelector('[data-jg-following]');
    expect(status).toHaveAttribute('data-jg-following', 'true');
    expect(status?.textContent).toMatch(/following the tail/i);
  });

  it('releases follow on scrolling back, and says so', () => {
    const { container } = render(<LogViewer label="Training log" lines={LINES} streaming />);
    scrolledBack(container);

    expect(container.querySelector('[data-jg-following]')).toHaveAttribute(
      'data-jg-following',
      'false',
    );
    expect(screen.getByText(/not following: scrolled back/i)).toBeInTheDocument();
  });

  it('offers a way back to the tail once it has been released', () => {
    const { container } = render(<LogViewer label="Training log" lines={LINES} streaming />);
    scrolledBack(container);

    expect(screen.getByRole('button', { name: /follow the tail/i })).toBeEnabled();
  });

  it('renders a window rather than every one of 200,000 lines (AC-X7)', () => {
    const many = Array.from({ length: 200_000 }, (_, index) => ({
      number: index + 1,
      text: `line ${String(index + 1)}`,
    }));
    const { container } = render(<LogViewer label="Training log" lines={many} />);
    expect(container.querySelectorAll('.jg-log__line').length).toBeLessThan(100);
  });
});

describe('Digest (AC-V8)', () => {
  const FULL = '9f2c4a10bd7e5583c1a04e6f9b28d3711ce4a0f5628d1b937ac4e5d0f16b8a23';

  it('shows eight characters', () => {
    const { container } = render(<Digest value={FULL} of="adapter" />);
    expect(container.querySelector('.jg-digest__value')?.textContent).toBe('9f2c4a10…');
  });

  it('keeps the whole of it reachable three ways', () => {
    const { container } = render(<Digest value={FULL} of="adapter" />);
    // For a mouse.
    expect(container.querySelector('.jg-digest__value')).toHaveAttribute('title', FULL);
    // For a screen reader, and for a search of the page.
    expect(container.textContent).toContain(FULL);
    // And for anybody pasting it into a ticket.
    expect(
      screen.getByRole('button', { name: /copy the full adapter digest/i }),
    ).toBeInTheDocument();
  });

  it('copies the whole value, never the truncation', () => {
    const copied: string[] = [];
    render(<Digest value={FULL} of="adapter" onCopy={(value) => copied.push(value)} />);
    fireEvent.click(screen.getByRole('button', { name: /copy/i }));
    expect(copied).toEqual([FULL]);
  });
});

describe('SpecEditor (section 5.2)', () => {
  it('never submits on Enter', async () => {
    const submitted: string[] = [];
    const user = userEvent.setup();
    const { container } = render(
      <SpecEditor
        label="Run specification"
        value='{"kind":"AdapterRun"}'
        onSubmit={(value) => submitted.push(value)}
      />,
    );

    const form = container.querySelector('form');
    expect(form).not.toBeNull();
    await user.click(screen.getByRole('textbox', { name: /specification/i }));
    await user.keyboard('{Enter}');
    fireEvent.submit(form as Element);

    // A specification is a multi-line document and Enter is how you get a new
    // line in one. Submitting on it turns an ordinary keystroke into days of
    // GPU time.
    expect(submitted).toEqual([]);
  });

  it('validates inline and names the pointer', () => {
    render(
      <SpecEditor
        label="Run specification"
        value='{"kind":"AdapterRun"}'
        validate={() => [{ pointer: '/train/params/rank', message: 'is required.' }]}
      />,
    );
    expect(screen.getByText('/train/params/rank')).toBeInTheDocument();
    expect(screen.getByText(/one problem with the specification/i)).toBeInTheDocument();
  });

  it('says what is wrong with unparseable JSON rather than only that it is', () => {
    render(<SpecEditor label="Run specification" value="{not json" />);
    expect(screen.getByText(/not valid json/i)).toBeInTheDocument();
  });

  it('makes dry run the primary action and holds submit back until it is done', async () => {
    const user = userEvent.setup();
    render(<SpecEditor label="Run specification" value='{"kind":"AdapterRun"}' />);

    // Exact, because the disabled Submit explains itself with a sentence that
    // begins "Dry run this specification first" and would otherwise match.
    const dryRun = screen.getByRole('button', { name: 'Dry run' });
    const submit = screen.getByRole('button', { name: /^Submit/ });

    expect(dryRun).toHaveAttribute('data-jg-variant', 'primary');
    expect(submit).toHaveAttribute('data-jg-variant', 'secondary');
    expect(submit).toBeDisabled();

    await user.click(dryRun);
    expect(screen.getByRole('button', { name: 'Submit' })).toBeEnabled();
  });

  it('refuses to run a specification that does not validate', () => {
    render(
      <SpecEditor
        label="Run specification"
        value='{"kind":"AdapterRun"}'
        validate={() => [{ pointer: '', message: 'no.' }]}
      />,
    );
    expect(screen.getByRole('button', { name: /^Dry run/ })).toBeDisabled();
  });
});
