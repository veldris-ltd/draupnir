import type { ChangeEvent, JSX, KeyboardEvent as ReactKeyboardEvent, ReactNode } from 'react';
import { useId, useState } from 'react';
import { StateSurface, isInert, type StateProps } from '../state/states';
import './primitives.css';

/**
 * The sixteen primitives of SAD 11F.1.
 *
 * Every one takes the shared `StateProps`, so `state="denied"` means the same
 * thing on a button as on a table. Two patterns, chosen per component by what
 * the state actually implies:
 *
 * - a control that *is* the thing (button, input, toggle) renders itself
 *   inert and keeps its shape, because replacing a disabled button with a
 *   panel loses the fact that the action exists at all;
 * - a container that *shows* things (table, tabs) hands its content to
 *   `StateSurface`, because an empty table with headers and no explanation is
 *   the exact ambiguity the six states exist to remove.
 */

// ---------------------------------------------------------------------------
// Button
// ---------------------------------------------------------------------------

export type ButtonVariant = 'primary' | 'secondary' | 'danger' | 'ghost';

export interface ButtonProps extends StateProps {
  children: ReactNode;
  variant?: ButtonVariant | undefined;
  size?: 'sm' | 'md' | undefined;
  type?: 'button' | 'submit' | undefined;
  onClick?: (() => void) | undefined;
  /**
   * This button dismisses the surface it sits on rather than acting on its
   * subject.
   *
   * A dismissal stays operable in every state. Disabling the Cancel of a
   * denied dialog, or the Close of a partitioned drawer, traps a keyboard user
   * inside a modal that has just told them there is nothing they can do -- a
   * worse failure than the one the disabling was meant to prevent. The
   * attribute exists so that the rule "every control is inert unless ready"
   * can be tested with its one exception named rather than assumed.
   */
  dismiss?: boolean | undefined;
}

/** A button. Inert in every state but `ready`, and it says why. */
export function Button({
  children,
  variant = 'primary',
  size = 'md',
  type = 'button',
  onClick,
  dismiss = false,
  state = 'ready',
  stateMessage,
}: ButtonProps): JSX.Element {
  const inert = isInert(state) && !dismiss;
  // A disabled control with no explanation is a dead end. The title and the
  // accessible description carry the reason, so a keyboard or screen reader
  // user gets it without hovering.
  const reason = inert ? (stateMessage ?? DISABLED_REASON[state]) : undefined;

  return (
    <button
      type={type}
      className="jg-button"
      data-jg-variant={variant}
      data-jg-size={size}
      data-jg-state={state}
      data-jg-dismiss={dismiss ? 'true' : undefined}
      disabled={inert}
      aria-disabled={inert || undefined}
      title={reason}
      onClick={inert ? undefined : onClick}
    >
      {state === 'loading' ? <span className="jg-button__spinner" aria-hidden="true" /> : null}
      <span>{children}</span>
      {reason === undefined ? null : <span className="jg-sr-only">. {reason}</span>}
    </button>
  );
}

/**
 * What a display element's state means, per state.
 *
 * Separate from `DISABLED_REASON` because the two say genuinely different
 * things: a button that cannot be pressed tells you to wait, a badge that
 * cannot be resolved tells you what is not known. Rendered visually hidden
 * beside the compact label -- a badge has room for one word and a screen
 * reader user needs the sentence.
 */
const STATE_EXPLANATION: Record<string, string | undefined> = {
  ready: undefined,
  loading: 'Loading.',
  empty: 'Nothing to show.',
  error: 'Could not be loaded because the last request failed.',
  denied: 'Not permitted: your role does not allow you to see this.',
  readOnly: 'Read only: you can see this and not change it.',
  partitioned: 'Unavailable: the site is partitioned from the federation.',
};

/** Why a control is inert, per state. Empty for `ready`. */
const DISABLED_REASON: Record<string, string | undefined> = {
  ready: undefined,
  loading: 'Working. Wait for this to finish.',
  empty: 'Unavailable: there is nothing to act on.',
  error: 'Unavailable: the last request failed.',
  denied: 'Not permitted: your role does not allow this.',
  readOnly: 'Read only: you can see this and not change it.',
  partitioned: 'Unavailable: the site is partitioned from the federation.',
};

// ---------------------------------------------------------------------------
// Field shell, input, select
// ---------------------------------------------------------------------------

interface FieldShellProps {
  id: string;
  label: string;
  hint?: string | undefined;
  error?: string | undefined;
  required?: boolean | undefined;
  children: ReactNode;
}

function FieldShell({ id, label, hint, error, required, children }: FieldShellProps): JSX.Element {
  return (
    <div className="jg-field">
      <label className="jg-field__label" htmlFor={id}>
        {label}
        {required === true ? (
          <span className="jg-field__required" aria-hidden="true">
            {' '}
            *
          </span>
        ) : null}
        {required === true ? <span className="jg-sr-only"> (required)</span> : null}
      </label>
      {children}
      {hint === undefined ? null : (
        <p className="jg-field__hint" id={`${id}-hint`}>
          {hint}
        </p>
      )}
      {/*
       * The error is a live region: a validation message that appears without
       * being announced is a message a screen reader user never receives.
       */}
      <p className="jg-field__error" id={`${id}-error`} role="alert">
        {error ?? ''}
      </p>
    </div>
  );
}

export interface InputProps extends StateProps {
  label: string;
  value?: string | undefined;
  placeholder?: string | undefined;
  hint?: string | undefined;
  error?: string | undefined;
  required?: boolean | undefined;
  onChange?: ((value: string) => void) | undefined;
}

/** A labelled text input. */
export function Input({
  label,
  value = '',
  placeholder,
  hint,
  error,
  required,
  onChange,
  state = 'ready',
  stateMessage,
}: InputProps): JSX.Element {
  const id = useId();
  const inert = isInert(state);
  const message = error ?? (inert ? (stateMessage ?? DISABLED_REASON[state]) : undefined);

  return (
    <FieldShell id={id} label={label} hint={hint} error={message} required={required}>
      <input
        id={id}
        className="jg-input"
        type="text"
        value={value}
        placeholder={state === 'loading' ? 'Loading…' : placeholder}
        disabled={inert}
        required={required}
        aria-invalid={error === undefined ? undefined : true}
        aria-describedby={
          [hint === undefined ? undefined : `${id}-hint`, `${id}-error`]
            .filter(Boolean)
            .join(' ') || undefined
        }
        onChange={(event: ChangeEvent<HTMLInputElement>) => {
          onChange?.(event.target.value);
        }}
      />
    </FieldShell>
  );
}

export interface SelectOption {
  value: string;
  label: string;
}

export interface SelectProps extends StateProps {
  label: string;
  options: SelectOption[];
  value?: string | undefined;
  hint?: string | undefined;
  error?: string | undefined;
  required?: boolean | undefined;
  onChange?: ((value: string) => void) | undefined;
}

/** A labelled select. */
export function Select({
  label,
  options,
  value,
  hint,
  error,
  required,
  onChange,
  state = 'ready',
  stateMessage,
}: SelectProps): JSX.Element {
  const id = useId();
  const inert = isInert(state);
  const message = error ?? (inert ? (stateMessage ?? DISABLED_REASON[state]) : undefined);
  const shown = state === 'empty' ? [] : options;

  return (
    <FieldShell id={id} label={label} hint={hint} error={message} required={required}>
      <select
        id={id}
        className="jg-select"
        value={value}
        disabled={inert}
        required={required}
        aria-invalid={error === undefined ? undefined : true}
        aria-describedby={
          [hint === undefined ? undefined : `${id}-hint`, `${id}-error`]
            .filter(Boolean)
            .join(' ') || undefined
        }
        onChange={(event: ChangeEvent<HTMLSelectElement>) => {
          onChange?.(event.target.value);
        }}
      >
        {shown.length === 0 ? (
          <option value="">No options available</option>
        ) : (
          shown.map((option) => (
            <option key={option.value} value={option.value}>
              {option.label}
            </option>
          ))
        )}
      </select>
    </FieldShell>
  );
}

// ---------------------------------------------------------------------------
// Checkbox, radio, toggle
// ---------------------------------------------------------------------------

export interface CheckboxProps extends StateProps {
  label: string;
  checked?: boolean | undefined;
  onChange?: ((checked: boolean) => void) | undefined;
}

/** A checkbox with its label. */
export function Checkbox({
  label,
  checked = false,
  onChange,
  state = 'ready',
}: CheckboxProps): JSX.Element {
  const inert = isInert(state);
  const reason = DISABLED_REASON[state];
  return (
    <label className="jg-choice" data-jg-state={state} title={reason}>
      <input
        type="checkbox"
        checked={checked}
        disabled={inert}
        onChange={(event) => {
          onChange?.(event.target.checked);
        }}
      />
      <span>{label}</span>
      {reason === undefined ? null : <span className="jg-sr-only">. {reason}</span>}
    </label>
  );
}

export interface RadioProps extends StateProps {
  label: string;
  name: string;
  value: string;
  checked?: boolean | undefined;
  onChange?: ((value: string) => void) | undefined;
}

/** One radio in a group. */
export function Radio({
  label,
  name,
  value,
  checked = false,
  onChange,
  state = 'ready',
}: RadioProps): JSX.Element {
  const inert = isInert(state);
  const reason = DISABLED_REASON[state];
  return (
    <label className="jg-choice" data-jg-state={state} title={reason}>
      <input
        type="radio"
        name={name}
        value={value}
        checked={checked}
        disabled={inert}
        onChange={() => {
          onChange?.(value);
        }}
      />
      <span>{label}</span>
      {reason === undefined ? null : <span className="jg-sr-only">. {reason}</span>}
    </label>
  );
}

export interface ToggleProps extends StateProps {
  label: string;
  checked?: boolean | undefined;
  onChange?: ((checked: boolean) => void) | undefined;
}

/**
 * A switch.
 *
 * `role="switch"` on a real button rather than a styled checkbox, so it is
 * keyboard operable by default and announces "on"/"off" rather than
 * "checked"/"unchecked" -- which is what a user expects of a setting that
 * takes effect immediately.
 */
export function Toggle({
  label,
  checked = false,
  onChange,
  state = 'ready',
  stateMessage,
}: ToggleProps): JSX.Element {
  const inert = isInert(state);
  const reason = stateMessage ?? DISABLED_REASON[state];

  return (
    <button
      type="button"
      role="switch"
      className="jg-toggle"
      aria-checked={checked}
      disabled={inert}
      title={reason}
      onClick={inert ? undefined : () => onChange?.(!checked)}
    >
      <span className="jg-toggle__track">
        <span className="jg-toggle__thumb" />
      </span>
      <span>{label}</span>
      {reason === undefined ? null : <span className="jg-sr-only">. {reason}</span>}
    </button>
  );
}

// ---------------------------------------------------------------------------
// Table
// ---------------------------------------------------------------------------

export interface Column<Row> {
  key: string;
  header: string;
  numeric?: boolean | undefined;
  render: (row: Row) => ReactNode;
}

export interface TableProps<Row> extends StateProps {
  caption: string;
  columns: Column<Row>[];
  rows: Row[];
  rowKey: (row: Row) => string;
}

/**
 * A data table.
 *
 * `scope="col"` on every header and a real `<caption>`, because SAD 11F.4
 * requires proper header association: without it a screen reader reads a grid
 * of values with no idea which column each belongs to, which for a gate result
 * table is worse than not reading it at all.
 */
export function Table<Row>({
  caption,
  columns,
  rows,
  rowKey,
  state = 'ready',
  stateMessage,
  problem,
}: TableProps<Row>): JSX.Element {
  const resolved = state === 'ready' && rows.length === 0 ? 'empty' : state;

  return (
    <StateSurface
      label={caption}
      state={resolved}
      stateMessage={stateMessage}
      problem={problem}
      minHeight="12rem"
    >
      <div className="jg-table-wrap">
        <table className="jg-table">
          <caption>{caption}</caption>
          <thead>
            <tr>
              {columns.map((column) => (
                <th key={column.key} scope="col" data-jg-numeric={column.numeric}>
                  {column.header}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <tr key={rowKey(row)}>
                {columns.map((column) => (
                  <td key={column.key} data-jg-numeric={column.numeric}>
                    {column.render(row)}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </StateSurface>
  );
}

// ---------------------------------------------------------------------------
// Badge and tag
// ---------------------------------------------------------------------------

export type Tone = 'neutral' | 'info' | 'success' | 'warning' | 'danger';

export interface BadgeProps extends StateProps {
  children: ReactNode;
  tone?: Tone | undefined;
}

/**
 * A status badge.
 *
 * The tone is carried by colour *and* by the text, never by colour alone
 * (WCAG 1.4.1): a badge reading "FAILED" in red still reads "FAILED" in
 * greyscale.
 */
export function Badge({ children, tone = 'neutral', state = 'ready' }: BadgeProps): JSX.Element {
  const shown = state === 'loading' ? '…' : state === 'ready' ? children : STATE_LABEL[state];
  const resolvedTone = state === 'ready' ? tone : STATE_TONE[state];

  return (
    <span className="jg-badge" data-jg-tone={resolvedTone} data-jg-state={state}>
      {shown}
      {state === 'ready' ? null : <span className="jg-sr-only">. {STATE_EXPLANATION[state]}</span>}
    </span>
  );
}

const STATE_LABEL: Record<string, string> = {
  loading: '…',
  empty: 'None',
  error: 'Error',
  denied: 'Hidden',
  readOnly: 'Read only',
  partitioned: 'Partitioned',
};

const STATE_TONE: Record<string, Tone> = {
  loading: 'neutral',
  empty: 'neutral',
  error: 'danger',
  denied: 'warning',
  readOnly: 'neutral',
  partitioned: 'info',
};

export interface TagProps extends StateProps {
  children: ReactNode;
  onRemove?: (() => void) | undefined;
  /**
   * The accessible name of the remove button.
   *
   * A tag's children can be any node, and `Remove [object Object]` is what
   * an icon child produces if the label is derived from them, so the caller
   * says what the thing is called.
   */
  removeLabel?: string | undefined;
}

/** A removable tag. */
export function Tag({ children, onRemove, removeLabel, state = 'ready' }: TagProps): JSX.Element {
  const inert = isInert(state);
  return (
    <span className="jg-tag" data-jg-state={state}>
      <span>{state === 'ready' ? children : STATE_LABEL[state]}</span>
      {state === 'ready' ? null : <span className="jg-sr-only">. {STATE_EXPLANATION[state]}</span>}
      {onRemove === undefined ? null : (
        <button
          type="button"
          className="jg-tag__remove"
          disabled={inert}
          aria-label={removeLabel ?? 'Remove'}
          onClick={inert ? undefined : onRemove}
        >
          ×
        </button>
      )}
    </span>
  );
}

// ---------------------------------------------------------------------------
// Tooltip
// ---------------------------------------------------------------------------

export interface TooltipProps extends StateProps {
  content: string;
  children: ReactNode;
}

/**
 * A tooltip.
 *
 * Opens on focus as well as hover, and the trigger is described by the bubble
 * rather than labelled by it -- a tooltip that replaces the accessible name
 * leaves the control unnamed when the tooltip is closed.
 */
export function Tooltip({ content, children, state = 'ready' }: TooltipProps): JSX.Element {
  const [open, setOpen] = useState(false);
  const id = useId();
  const text = state === 'ready' ? content : (DISABLED_REASON[state] ?? content);

  return (
    <span
      className="jg-tooltip"
      onMouseEnter={() => {
        setOpen(true);
      }}
      onMouseLeave={() => {
        setOpen(false);
      }}
      onFocus={() => {
        setOpen(true);
      }}
      onBlur={() => {
        setOpen(false);
      }}
    >
      <span aria-describedby={id}>{children}</span>
      {open ? (
        <span className="jg-tooltip__bubble" role="tooltip" id={id}>
          {text}
        </span>
      ) : (
        <span className="jg-sr-only" id={id}>
          {text}
        </span>
      )}
    </span>
  );
}

// ---------------------------------------------------------------------------
// Dialog and drawer
// ---------------------------------------------------------------------------

export interface DialogProps extends StateProps {
  title: string;
  children: ReactNode;
  /**
   * The consequence, in words. SAD 11F.3: a destructive action is two step
   * with the consequence stated rather than a generic confirmation, because
   * "Are you sure?" tells an operator nothing they did not already know.
   */
  consequence?: string | undefined;
  confirmLabel?: string | undefined;
  onConfirm?: (() => void) | undefined;
  onDismiss?: (() => void) | undefined;
}

/** A modal dialog. */
export function Dialog({
  title,
  children,
  consequence,
  confirmLabel = 'Confirm',
  onConfirm,
  onDismiss,
  state = 'ready',
  stateMessage,
  problem,
}: DialogProps): JSX.Element {
  const titleId = useId();
  const bodyId = useId();

  return (
    <div className="jg-scrim">
      <div
        className="jg-dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        aria-describedby={bodyId}
      >
        <h2 className="jg-dialog__title" id={titleId}>
          {title}
        </h2>
        <div id={bodyId}>
          <StateSurface
            label={title}
            state={state}
            stateMessage={stateMessage}
            problem={problem}
            minHeight="6rem"
          >
            <div className="jg-dialog__body">{children}</div>
            {consequence === undefined ? null : (
              <p className="jg-dialog__consequence">{consequence}</p>
            )}
          </StateSurface>
        </div>
        <div className="jg-dialog__actions">
          <Button variant="secondary" dismiss onClick={onDismiss}>
            Cancel
          </Button>
          <Button variant="danger" state={state} onClick={onConfirm}>
            {confirmLabel}
          </Button>
        </div>
      </div>
    </div>
  );
}

export interface DrawerProps extends StateProps {
  title: string;
  children: ReactNode;
  onClose?: (() => void) | undefined;
}

/** A side drawer. */
export function Drawer({
  title,
  children,
  onClose,
  state = 'ready',
  stateMessage,
  problem,
}: DrawerProps): JSX.Element {
  const titleId = useId();
  return (
    <aside className="jg-drawer" role="dialog" aria-labelledby={titleId}>
      <header className="jg-drawer__header">
        <h2 className="jg-drawer__title" id={titleId}>
          {title}
        </h2>
        <Button variant="ghost" size="sm" dismiss onClick={onClose}>
          Close
        </Button>
      </header>
      <div className="jg-drawer__body">
        <StateSurface
          label={title}
          state={state}
          stateMessage={stateMessage}
          problem={problem}
          minHeight="10rem"
        >
          {children}
        </StateSurface>
      </div>
    </aside>
  );
}

// ---------------------------------------------------------------------------
// Toast
// ---------------------------------------------------------------------------

export interface ToastProps extends StateProps {
  title: string;
  detail?: string | undefined;
  tone?: Tone | undefined;
  onDismiss?: (() => void) | undefined;
}

/**
 * One toast.
 *
 * `role="status"` with `aria-live="polite"` for anything informational, and
 * `role="alert"` for a failure: an error a user is not told about is an error
 * they act on the absence of.
 */
export function Toast({
  title,
  detail,
  tone = 'info',
  onDismiss,
  state = 'ready',
}: ToastProps): JSX.Element {
  const resolvedTone = state === 'ready' ? tone : STATE_TONE[state];
  const shownTitle = state === 'ready' ? title : STATE_LABEL[state];
  const alerting = resolvedTone === 'danger';

  return (
    <div
      className="jg-toast"
      data-jg-tone={resolvedTone}
      role={alerting ? 'alert' : 'status'}
      aria-live={alerting ? 'assertive' : 'polite'}
    >
      <div className="jg-toast__body">
        <p className="jg-toast__title">{shownTitle}</p>
        {detail === undefined ? null : <p className="jg-toast__detail">{detail}</p>}
      </div>
      <Button variant="ghost" size="sm" dismiss onClick={onDismiss}>
        Dismiss
      </Button>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Tabs
// ---------------------------------------------------------------------------

export interface TabItem {
  id: string;
  label: string;
  content: ReactNode;
}

export interface TabsProps extends StateProps {
  label: string;
  items: TabItem[];
  activeId?: string | undefined;
  onSelect?: ((id: string) => void) | undefined;
}

/**
 * A tab set.
 *
 * Arrow keys move between tabs and only the selected tab is in the tab order,
 * per the ARIA authoring practice: a tab list where every tab is tabbable
 * makes a keyboard user press Tab eight times to leave a panel.
 */
export function Tabs({
  label,
  items,
  activeId,
  onSelect,
  state = 'ready',
  stateMessage,
  problem,
}: TabsProps): JSX.Element {
  const [internal, setInternal] = useState(items[0]?.id ?? '');
  const active = activeId ?? internal;
  const inert = isInert(state);

  function select(id: string): void {
    setInternal(id);
    onSelect?.(id);
  }

  function onKeyDown(event: ReactKeyboardEvent<HTMLButtonElement>): void {
    const index = items.findIndex((item) => item.id === active);
    if (index < 0) return;
    const step = event.key === 'ArrowRight' ? 1 : event.key === 'ArrowLeft' ? -1 : 0;
    if (step === 0) return;
    const next = items[(index + step + items.length) % items.length];
    if (next !== undefined) {
      select(next.id);
      event.preventDefault();
    }
  }

  const current = items.find((item) => item.id === active);

  return (
    <div className="jg-tabs">
      <div className="jg-tabs__list" role="tablist" aria-label={label}>
        {items.map((item) => (
          <button
            key={item.id}
            type="button"
            role="tab"
            className="jg-tabs__tab"
            id={`tab-${item.id}`}
            aria-selected={item.id === active}
            aria-controls={`panel-${item.id}`}
            tabIndex={item.id === active ? 0 : -1}
            disabled={inert}
            onKeyDown={onKeyDown}
            onClick={() => {
              select(item.id);
            }}
          >
            {item.label}
          </button>
        ))}
      </div>
      <div
        className="jg-tabs__panel"
        role="tabpanel"
        id={`panel-${active}`}
        aria-labelledby={`tab-${active}`}
        tabIndex={0}
      >
        <StateSurface
          label={label}
          state={state}
          stateMessage={stateMessage}
          problem={problem}
          minHeight="8rem"
        >
          {current?.content}
        </StateSurface>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Breadcrumb
// ---------------------------------------------------------------------------

export interface Crumb {
  label: string;
  href?: string | undefined;
}

export interface BreadcrumbProps extends StateProps {
  items: Crumb[];
}

/** A breadcrumb trail. The last item is `aria-current="page"`. */
export function Breadcrumb({ items, state = 'ready' }: BreadcrumbProps): JSX.Element {
  const shown: Crumb[] =
    state === 'ready' || state === 'readOnly' ? items : [{ label: STATE_LABEL[state] ?? state }];

  return (
    <nav className="jg-breadcrumb" aria-label="Breadcrumb" data-jg-state={state}>
      {state === 'ready' || state === 'readOnly' ? null : (
        <span className="jg-sr-only">{STATE_EXPLANATION[state]} </span>
      )}
      <ol className="jg-breadcrumb__list">
        {shown.map((crumb, index) => {
          const last = index === shown.length - 1;
          return (
            <li key={crumb.label}>
              {index > 0 ? (
                <span className="jg-breadcrumb__separator" aria-hidden="true">
                  /{' '}
                </span>
              ) : null}
              {crumb.href !== undefined && !last ? (
                <a href={crumb.href}>{crumb.label}</a>
              ) : (
                <span className="jg-breadcrumb__current" aria-current={last ? 'page' : undefined}>
                  {crumb.label}
                </span>
              )}
            </li>
          );
        })}
      </ol>
    </nav>
  );
}

// ---------------------------------------------------------------------------
// Pagination
// ---------------------------------------------------------------------------

export interface PaginationProps extends StateProps {
  label: string;
  /** How many items this page holds. */
  shown: number;
  /** The cursor for the next page, or null on the last one. */
  nextCursor?: string | null | undefined;
  hasPrevious?: boolean | undefined;
  onNext?: (() => void) | undefined;
  onPrevious?: (() => void) | undefined;
}

/**
 * Cursor pagination controls.
 *
 * Previous and next, never page numbers. A numbered pager is an offset pager,
 * and SAD 11E.2 rules offset out because it silently skips rows over a growing
 * ledger -- so there is deliberately no way to render one from this system.
 */
export function Pagination({
  label,
  shown,
  nextCursor = null,
  hasPrevious = false,
  onNext,
  onPrevious,
  state = 'ready',
}: PaginationProps): JSX.Element {
  const inert = isInert(state);
  return (
    <nav className="jg-pagination" aria-label={label}>
      <p aria-live="polite">
        {state === 'ready'
          ? `Showing ${String(shown)} ${shown === 1 ? 'item' : 'items'}`
          : STATE_LABEL[state]}
      </p>
      <div className="jg-pagination__controls">
        <Button
          variant="secondary"
          size="sm"
          state={inert || !hasPrevious ? 'readOnly' : 'ready'}
          onClick={onPrevious}
        >
          Previous
        </Button>
        <Button
          variant="secondary"
          size="sm"
          state={inert || nextCursor === null ? 'readOnly' : 'ready'}
          onClick={onNext}
        >
          Next
        </Button>
      </div>
    </nav>
  );
}
