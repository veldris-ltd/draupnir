import type { ChangeEvent, JSX, KeyboardEvent as ReactKeyboardEvent, ReactNode } from 'react';
import { useCallback, useEffect, useId, useMemo, useRef, useState } from 'react';
import { StateSurface, isInert, type StateProps } from '../state/states';
import { RUN_STATE_ATTRIBUTE, type RunState } from '../tokens';
import './primitives.css';

/**
 * The eighteen primitives of VLD-UX-DRAUPNIR-001 section 5.1.
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
// Focus management
// ---------------------------------------------------------------------------

/**
 * What counts as focusable inside a modal surface.
 *
 * Queried rather than tracked, because the content of a dialog changes while
 * it is open -- a form grows an error, a state surface replaces the body -- and
 * a list captured on mount goes stale the first time it does.
 */
const FOCUSABLE = [
  'a[href]',
  'button:not([disabled])',
  'input:not([disabled])',
  'select:not([disabled])',
  'textarea:not([disabled])',
  '[tabindex]:not([tabindex="-1"])',
].join(',');

function focusableWithin(container: HTMLElement | null): HTMLElement[] {
  if (container === null) return [];
  return [...container.querySelectorAll<HTMLElement>(FOCUSABLE)].filter(
    (element) => element.offsetParent !== null || element === document.activeElement,
  );
}

interface FocusTrap {
  /** Put on the surface that owns the trap. */
  ref: (node: HTMLElement | null) => void;
  /** Put on that surface: Tab wraps, Escape closes where it may. */
  onKeyDown: (event: ReactKeyboardEvent<HTMLElement>) => void;
}

/**
 * Trap focus inside a surface, and give it back on close. AC-X4.
 *
 * Three things, and the third is the one usually missed. Focus moves into the
 * surface when it opens; Tab and Shift+Tab wrap at the ends rather than
 * escaping to the page behind; and the element that had focus before the
 * surface opened gets it back when the surface goes away. Without the third, a
 * keyboard user who closes a dialog is returned to the top of the document and
 * has to find their place again.
 *
 * `escapes` is false for a destructive dialog. Section 5.1: "escape closes
 * unless destructive" -- a key pressed by accident should not be able to
 * dismiss the one surface that exists to slow somebody down, and the dialog
 * still has a Cancel button that can.
 */
function useFocusTrap(onClose: (() => void) | undefined, escapes: boolean): FocusTrap {
  const surface = useRef<HTMLElement | null>(null);
  const restoreTo = useRef<HTMLElement | null>(null);

  const ref = useCallback((node: HTMLElement | null) => {
    surface.current = node;
    if (node === null) return;
    // Captured on the render that mounts the surface, which is the last moment
    // the previously focused element is still focused.
    restoreTo.current ??=
      document.activeElement instanceof HTMLElement ? document.activeElement : null;
    const [first] = focusableWithin(node);
    (first ?? node).focus();
  }, []);

  useEffect(
    () => () => {
      // On unmount, whether the surface was dismissed or the whole screen went
      // away. `isConnected` because the element that had focus may itself have
      // been removed while the dialog was open.
      const target = restoreTo.current;
      if (target?.isConnected === true) target.focus();
    },
    [],
  );

  const onKeyDown = useCallback(
    (event: ReactKeyboardEvent<HTMLElement>) => {
      if (event.key === 'Escape' && escapes) {
        event.stopPropagation();
        onClose?.();
        return;
      }
      if (event.key !== 'Tab') return;

      const focusable = focusableWithin(surface.current);
      if (focusable.length === 0) return;
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (first === undefined || last === undefined) return;

      const active = document.activeElement;
      if (event.shiftKey && active === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && active === last) {
        event.preventDefault();
        first.focus();
      }
    },
    [onClose, escapes],
  );

  return { ref, onKeyDown };
}

// ---------------------------------------------------------------------------
// Button
// ---------------------------------------------------------------------------

export type ButtonVariant = 'primary' | 'secondary' | 'danger' | 'ghost';

interface ButtonBase extends StateProps {
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
  /**
   * A glyph beside the label. Always `aria-hidden`: an icon is decoration
   * beside a name, never the name itself.
   */
  icon?: ReactNode | undefined;
}

/**
 * A button, in two shapes, and the type is what keeps the second one safe.
 *
 * Section 5.1: "no icon only button without an accessible name". So an
 * icon-only button is a separate member of the union whose `children` must be
 * a string, and that string becomes the accessible name rather than visible
 * text. `<Button iconOnly icon={<Cross />}>{someNode}</Button>` does not
 * compile, which is a better enforcement than a runtime warning nobody reads.
 */
export type ButtonProps =
  | (ButtonBase & { iconOnly?: false | undefined; children: ReactNode })
  | (ButtonBase & { iconOnly: true; icon: ReactNode; children: string });

/** A button. Inert in every state but `ready`, and it says why. */
export function Button({
  children,
  variant = 'primary',
  size = 'md',
  type = 'button',
  onClick,
  dismiss = false,
  icon,
  iconOnly = false,
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
      data-jg-icon-only={iconOnly ? 'true' : undefined}
      disabled={inert}
      aria-disabled={inert || undefined}
      title={iconOnly ? (reason ?? (children as string)) : reason}
      onClick={inert ? undefined : onClick}
    >
      {state === 'loading' ? <span className="jg-button__spinner" aria-hidden="true" /> : null}
      {icon === undefined ? null : (
        <span className="jg-button__icon" aria-hidden="true">
          {icon}
        </span>
      )}
      <span className={iconOnly ? 'jg-sr-only' : undefined}>{children}</span>
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

export interface TextAreaProps extends StateProps {
  label: string;
  value?: string | undefined;
  placeholder?: string | undefined;
  hint?: string | undefined;
  error?: string | undefined;
  required?: boolean | undefined;
  rows?: number | undefined;
  onChange?: ((value: string) => void) | undefined;
}

/**
 * A labelled multi-line input.
 *
 * The same shell as `Input`, and for the same reason section 5.1 gives: the
 * label is always visible and the placeholder never stands in for it. A
 * placeholder disappears the moment somebody types, so a field labelled only
 * by one is a field nobody can check their own answer against -- and it is
 * invisible to a screen reader in most implementations besides.
 */
export function TextArea({
  label,
  value = '',
  placeholder,
  hint,
  error,
  required,
  rows = 4,
  onChange,
  state = 'ready',
  stateMessage,
}: TextAreaProps): JSX.Element {
  const id = useId();
  const inert = isInert(state);
  const message = error ?? (inert ? (stateMessage ?? DISABLED_REASON[state]) : undefined);

  return (
    <FieldShell id={id} label={label} hint={hint} error={message} required={required}>
      <textarea
        id={id}
        className="jg-input jg-textarea"
        rows={rows}
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
        onChange={(event: ChangeEvent<HTMLTextAreaElement>) => {
          onChange?.(event.target.value);
        }}
      />
    </FieldShell>
  );
}

// ---------------------------------------------------------------------------
// Combobox
// ---------------------------------------------------------------------------

/**
 * Above this many options, section 5.1 asks for a combobox rather than a
 * native select. Exported so a screen chooses by the rule rather than by eye,
 * and so the rule is one number in one place.
 */
export const COMBOBOX_THRESHOLD = 12;

/** Whether an option set wants a combobox. Section 5.1. */
export function wantsCombobox(options: readonly unknown[]): boolean {
  return options.length > COMBOBOX_THRESHOLD;
}

export interface ComboboxProps extends StateProps {
  label: string;
  options: SelectOption[];
  value?: string | undefined;
  placeholder?: string | undefined;
  hint?: string | undefined;
  error?: string | undefined;
  required?: boolean | undefined;
  /** Announced when the filter matches nothing. Names what would be here. */
  emptyMessage?: string | undefined;
  onChange?: ((value: string) => void) | undefined;
}

/**
 * A filtering combobox, for an option set too long to scan in a native select.
 *
 * The ARIA 1.2 pattern, not a text box beside a div. `role="combobox"` on the
 * input, `aria-expanded`, `aria-controls` to the listbox, and
 * `aria-activedescendant` pointing at the highlighted option -- so focus stays
 * in the input while the arrow keys move the selection, which is what lets
 * somebody keep typing to narrow the list.
 *
 * The filter is a plain case-insensitive substring. A fuzzy match reorders
 * results in a way that makes the first press of Down land on something
 * different each time, and an operator picking a jurisdiction from a list of
 * fifty-six wants the list to stay where they left it.
 */
export function Combobox({
  label,
  options,
  value = '',
  placeholder,
  hint,
  error,
  required,
  emptyMessage = 'No option matches that.',
  onChange,
  state = 'ready',
  stateMessage,
}: ComboboxProps): JSX.Element {
  const id = useId();
  const inert = isInert(state);
  const message = error ?? (inert ? (stateMessage ?? DISABLED_REASON[state]) : undefined);

  const selected = options.find((option) => option.value === value);
  const [query, setQuery] = useState(selected?.label ?? '');
  const [open, setOpen] = useState(false);
  /**
   * Which option the arrow keys are on, or null when none of them is.
   *
   * Null rather than zero when the list opens. The ARIA combobox pattern has
   * the first Down Arrow move to the *first* option, so pre-highlighting one
   * on open would make the first press skip it -- and would put a highlight on
   * a row the user has not moved to, which reads as a selection they did not
   * make.
   */
  const [active, setActive] = useState<number | null>(null);

  const matches = useMemo(() => {
    const needle = query.trim().toLowerCase();
    if (needle === '') return options;
    return options.filter((option) => option.label.toLowerCase().includes(needle));
  }, [options, query]);

  const choose = (option: SelectOption | undefined): void => {
    if (option === undefined) return;
    setQuery(option.label);
    setOpen(false);
    onChange?.(option.value);
  };

  const onKeyDown = (event: ReactKeyboardEvent<HTMLInputElement>): void => {
    if (inert) return;
    if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
      event.preventDefault();
      setOpen(true);
      if (matches.length === 0) return;
      const step = event.key === 'ArrowDown' ? 1 : -1;
      setActive((current) => {
        if (current === null) return step === 1 ? 0 : matches.length - 1;
        return (current + step + matches.length) % matches.length;
      });
    } else if (event.key === 'Enter' && open && active !== null) {
      event.preventDefault();
      choose(matches[active]);
    } else if (event.key === 'Escape' && open) {
      // Stopped here so a combobox inside a dialog closes its own list first.
      // Escape closing both at once loses the list the user was reading.
      event.stopPropagation();
      setOpen(false);
    } else if (event.key === 'Home' && open) {
      event.preventDefault();
      setActive(0);
    } else if (event.key === 'End' && open) {
      event.preventDefault();
      setActive(Math.max(matches.length - 1, 0));
    }
  };

  const activeId =
    active === null || matches[active] === undefined ? undefined : `${id}-option-${String(active)}`;

  return (
    <FieldShell id={id} label={label} hint={hint} error={message} required={required}>
      <div className="jg-combobox">
        <input
          id={id}
          className="jg-input"
          type="text"
          role="combobox"
          autoComplete="off"
          value={state === 'loading' ? '' : query}
          placeholder={state === 'loading' ? 'Loading…' : placeholder}
          disabled={inert}
          required={required}
          aria-expanded={open}
          aria-controls={`${id}-listbox`}
          aria-autocomplete="list"
          aria-activedescendant={open ? activeId : undefined}
          aria-invalid={error === undefined ? undefined : true}
          aria-describedby={
            [hint === undefined ? undefined : `${id}-hint`, `${id}-error`]
              .filter(Boolean)
              .join(' ') || undefined
          }
          onChange={(event: ChangeEvent<HTMLInputElement>) => {
            setQuery(event.target.value);
            // Typing re-filters, so whatever was highlighted is a row that may
            // no longer be there. Cleared rather than clamped to zero.
            setActive(null);
            setOpen(true);
          }}
          onFocus={() => {
            if (!inert) setOpen(true);
          }}
          onBlur={() => {
            setOpen(false);
          }}
          onKeyDown={onKeyDown}
        />
        {/*
         * Rendered whether or not it is open, so the listbox `aria-controls`
         * points at always exists. A reference to a missing element is an
         * `aria-controls` that resolves to nothing, which some screen readers
         * report as a broken control rather than a closed one.
         */}
        <ul
          className="jg-combobox__list"
          id={`${id}-listbox`}
          role="listbox"
          aria-label={label}
          hidden={!open || inert}
        >
          {matches.length === 0 ? (
            <li className="jg-combobox__empty" role="presentation">
              {emptyMessage}
            </li>
          ) : (
            matches.map((option, index) => (
              <li
                key={option.value}
                id={`${id}-option-${String(index)}`}
                role="option"
                className="jg-combobox__option"
                aria-selected={option.value === value}
                data-jg-active={index === active ? 'true' : undefined}
                // The list closes on blur, so a click has to land before the
                // input loses focus or the option is gone by the time it fires.
                onMouseDown={(event) => {
                  event.preventDefault();
                  choose(option);
                }}
              >
                {option.label}
              </li>
            ))
          )}
        </ul>
      </div>
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
  /**
   * What this column sorts and type-ahead searches on. A column without one
   * is not sortable, which is the honest default: a cell that renders a pill
   * and a digest has no obvious order, and inventing one from its markup
   * produces a sort nobody can predict.
   */
  sortKey?: ((row: Row) => string | number) | undefined;
  /** Hidden by default. Column visibility is the caller's to drive. */
  hidden?: boolean | undefined;
}

export type SortDirection = 'ascending' | 'descending';

export interface Sort {
  key: string;
  direction: SortDirection;
}

export interface TableProps<Row> extends StateProps {
  caption: string;
  columns: Column<Row>[];
  rows: Row[];
  rowKey: (row: Row) => string;
  /** The current sort. Uncontrolled when omitted. */
  sort?: Sort | undefined;
  onSortChange?: ((sort: Sort) => void) | undefined;
  /** Which column type-ahead matches on. Defaults to the first sortable one. */
  searchKey?: string | undefined;
}

/** How long a type-ahead buffer survives without a keystroke. */
const TYPE_AHEAD_MS = 1000;

function compare(a: string | number, b: string | number): number {
  if (typeof a === 'number' && typeof b === 'number') return a - b;
  return String(a).localeCompare(String(b), 'en-GB', { numeric: true, sensitivity: 'base' });
}

/**
 * A data table.
 *
 * `scope="col"` on every header and a real `<caption>`, because SAD 11F.4
 * requires proper header association: without it a screen reader reads a grid
 * of values with no idea which column each belongs to, which for a gate result
 * table is worse than not reading it at all.
 *
 * **Keyboard.** One tab stop for the whole grid, and the arrow keys move
 * within it. A table of two hundred runs with a tab stop per row is a table
 * nobody tabs past. Up and down move a row, Home and End go to the ends, Page
 * Up and Page Down move ten, and typing letters jumps to the next row starting
 * with them -- the behaviour of every file list anybody has used, which is why
 * it needs no instructions.
 *
 * **Sorting.** `aria-sort` on the header cell, and the button inside it is
 * what a keyboard user presses; the sort state is announced because the
 * attribute is on the header rather than only in the arrow glyph.
 *
 * **Row height** comes from the density token, so compact is a density choice
 * rather than a second table.
 */
export function Table<Row>({
  caption,
  columns,
  rows,
  rowKey,
  sort,
  onSortChange,
  searchKey,
  state = 'ready',
  stateMessage,
  problem,
}: TableProps<Row>): JSX.Element {
  const resolved = state === 'ready' && rows.length === 0 ? 'empty' : state;
  const shown = useMemo(() => columns.filter((column) => column.hidden !== true), [columns]);

  const [ownSort, setOwnSort] = useState<Sort | undefined>(undefined);
  const active = sort ?? ownSort;

  const ordered = useMemo(() => {
    if (active === undefined) return rows;
    const column = shown.find((item) => item.key === active.key);
    const key = column?.sortKey;
    if (key === undefined) return rows;
    const sorted = [...rows].sort((a, b) => compare(key(a), key(b)));
    return active.direction === 'descending' ? sorted.reverse() : sorted;
  }, [rows, shown, active]);

  const [focused, setFocused] = useState(0);
  const buffer = useRef({ text: '', at: 0 });
  const bodyRef = useRef<HTMLTableSectionElement | null>(null);

  const searchColumn =
    shown.find((column) => column.key === searchKey) ??
    shown.find((column) => column.sortKey !== undefined);

  const focusRow = useCallback((index: number) => {
    setFocused(index);
    const row = bodyRef.current?.children.item(index);
    if (row instanceof HTMLElement) row.focus();
  }, []);

  const toggleSort = (column: Column<Row>): void => {
    if (column.sortKey === undefined) return;
    const direction: SortDirection =
      active?.key === column.key && active.direction === 'ascending' ? 'descending' : 'ascending';
    const next: Sort = { key: column.key, direction };
    setOwnSort(next);
    onSortChange?.(next);
  };

  const onKeyDown = (event: ReactKeyboardEvent<HTMLTableSectionElement>): void => {
    const last = ordered.length - 1;
    if (last < 0) return;

    const move = (to: number): void => {
      event.preventDefault();
      focusRow(Math.min(Math.max(to, 0), last));
    };

    const TO: Record<string, number | undefined> = {
      ArrowDown: focused + 1,
      ArrowUp: focused - 1,
      Home: 0,
      End: last,
      PageDown: focused + 10,
      PageUp: focused - 10,
    };
    const to = TO[event.key];
    if (to !== undefined) {
      move(to);
      return;
    }

    // Type-ahead. One printable character at a time, accumulated while the
    // typing continues, so "dv" finds dvalin and not the next row starting d.
    if (event.key.length !== 1 || event.ctrlKey || event.metaKey || event.altKey) return;
    const key = searchColumn?.sortKey;
    if (key === undefined) return;

    const now = Date.now();
    const text =
      now - buffer.current.at > TYPE_AHEAD_MS
        ? event.key.toLowerCase()
        : buffer.current.text + event.key.toLowerCase();
    buffer.current = { text, at: now };

    // Search from the row after the focused one and wrap, so repeating a
    // letter walks through the rows that share it.
    for (let step = 1; step <= ordered.length; step += 1) {
      const index = (focused + step) % ordered.length;
      const row = ordered[index];
      if (row === undefined) continue;
      if (String(key(row)).toLowerCase().startsWith(text)) {
        event.preventDefault();
        focusRow(index);
        return;
      }
    }
  };

  return (
    <StateSurface
      label={caption}
      state={resolved}
      stateMessage={stateMessage}
      problem={problem}
      reserve="lg"
    >
      <div className="jg-table-wrap">
        <table className="jg-table">
          <caption>
            {caption}
            <span className="jg-sr-only">
              . Use the arrow keys to move between rows, and type to jump to a row.
            </span>
          </caption>
          <thead>
            <tr>
              {shown.map((column) => {
                const sortable = column.sortKey !== undefined;
                const direction = active?.key === column.key ? active.direction : undefined;
                return (
                  <th
                    key={column.key}
                    scope="col"
                    data-jg-numeric={column.numeric}
                    aria-sort={sortable ? (direction ?? 'none') : undefined}
                  >
                    {sortable ? (
                      <button
                        type="button"
                        className="jg-table__sort"
                        onClick={() => {
                          toggleSort(column);
                        }}
                      >
                        {column.header}
                        <span className="jg-table__sort-glyph" aria-hidden="true">
                          {direction === 'ascending' ? '▲' : direction === 'descending' ? '▼' : '↕'}
                        </span>
                      </button>
                    ) : (
                      column.header
                    )}
                  </th>
                );
              })}
            </tr>
          </thead>
          {/*
           * The handler is on the body rather than on each row because the
           * grid is one keyboard widget: a key pressed anywhere in it moves
           * the roving tabindex. `jsx-a11y` cannot express the WAI-ARIA grid
           * pattern -- it sees a listener on a `tbody` and stops there -- and
           * the rows it delegates to are focusable, which is the property the
           * rule is actually protecting.
           */}
          {/* eslint-disable-next-line jsx-a11y/no-noninteractive-element-interactions */}
          <tbody ref={bodyRef} onKeyDown={onKeyDown}>
            {ordered.map((row, index) => (
              <tr
                key={rowKey(row)}
                // One tab stop for the grid: the focused row is reachable and
                // the rest are not, so Tab leaves the table rather than walking
                // it. WAI-ARIA calls this a roving tabindex.
                tabIndex={index === focused ? 0 : -1}
                data-jg-focused={index === focused ? 'true' : undefined}
                onFocus={() => {
                  setFocused(index);
                }}
              >
                {shown.map((column) => (
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

export interface PillProps extends StateProps {
  /** The lifecycle state this pill names. */
  runState: RunState;
}

/**
 * A run state, in the one appearance the whole system uses for it.
 *
 * Section 5.1: "carries the state token. Text always present." Both halves
 * matter. The colour comes from `tokens/state.css` by way of the attribute --
 * this component knows no colours, which is what AC-V4 asks for -- and the
 * label is inside the pill, so the state survives greyscale, a colour vision
 * deficiency and a printout.
 *
 * The marker dot is `aria-hidden`. It repeats what the label says, and a
 * screen reader reading "circle TRAINING" is worse than one reading "TRAINING".
 */
export function Pill({ runState, state = 'ready', stateMessage }: PillProps): JSX.Element {
  const inert = state !== 'ready';
  const explanation = inert ? (stateMessage ?? STATE_EXPLANATION[state]) : undefined;

  return (
    <span
      className="jg-state-pill"
      {...{ [RUN_STATE_ATTRIBUTE]: runState }}
      data-jg-state={state}
      data-jg-target="inline"
    >
      <span className="jg-state-pill__marker" aria-hidden="true" />
      {inert ? STATE_LABEL[state] : runState.replace(/_/g, ' ')}
      {explanation === undefined ? null : <span className="jg-sr-only">. {explanation}</span>}
    </span>
  );
}

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

/**
 * A modal dialog. Focus is trapped and restored (AC-X4).
 *
 * Escape closes it, unless it is destructive. A dialog with a `consequence` is
 * the second step of a two-step destructive action, and a key pressed by
 * accident should not be able to dismiss the thing that exists to slow
 * somebody down. Cancel still closes it, and says so.
 */
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
  const destructive = consequence !== undefined;
  const trap = useFocusTrap(onDismiss, !destructive);

  return (
    <div className="jg-scrim">
      {/*
       * A modal dialog has to hear Tab and Escape at its own boundary: that is
       * what trapping focus means, and there is nowhere else to put the
       * listener. `jsx-a11y` reads `role="dialog"` as non-interactive, which is
       * true of its content and not of the surface that owns the trap.
       */}
      {/* eslint-disable-next-line jsx-a11y/no-noninteractive-element-interactions */}
      <div
        className="jg-dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        aria-describedby={bodyId}
        ref={trap.ref}
        onKeyDown={trap.onKeyDown}
        tabIndex={-1}
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
            reserve="sm"
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
          {destructive ? (
            <p className="jg-sr-only">
              This is a destructive action, so escape does not close this dialog. Use cancel.
            </p>
          ) : null}
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

/**
 * A side drawer: detail beside a list, without losing the list.
 *
 * Focus is trapped and restored like a dialog's, and escape closes it. A
 * drawer is never the second step of a destructive action -- that is what a
 * dialog is for -- so there is no case here where escape should not work.
 */
export function Drawer({
  title,
  children,
  onClose,
  state = 'ready',
  stateMessage,
  problem,
}: DrawerProps): JSX.Element {
  const titleId = useId();
  const trap = useFocusTrap(onClose, true);
  return (
    /* eslint-disable-next-line jsx-a11y/no-noninteractive-element-interactions --
       As for the dialog: the surface that traps focus is the surface that has
       to hear Tab and Escape. */
    <aside
      className="jg-drawer"
      role="dialog"
      aria-labelledby={titleId}
      ref={trap.ref}
      onKeyDown={trap.onKeyDown}
      tabIndex={-1}
    >
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
          reserve="md"
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

/**
 * What a toast may be.
 *
 * `danger` is deliberately not here. Section 5.1: a toast carries "transient
 * confirmations only. Never an error that requires action." A toast that
 * disappears after five seconds is the worst possible carrier for something a
 * user has to do -- they may be looking elsewhere, they cannot get it back,
 * and there is nowhere in it to put the action. Errors belong to the surface
 * that failed, where the six states put them, with a correlation identifier
 * and something to press.
 *
 * Excluded in the type rather than checked at runtime, so a screen that tries
 * fails to compile.
 */
export type ToastTone = Exclude<Tone, 'danger'>;

export interface ToastProps extends StateProps {
  title: string;
  detail?: string | undefined;
  tone?: ToastTone | undefined;
  onDismiss?: (() => void) | undefined;
}

/**
 * One toast. A confirmation that something happened, and nothing else.
 *
 * `role="status"` and `aria-live="polite"`, never `alert` and never
 * `assertive`: a polite region waits for a pause in what the screen reader is
 * already saying, which is the correct behaviour for a confirmation and the
 * wrong one for an emergency. There are no emergencies in here by
 * construction.
 */
export function Toast({
  title,
  detail,
  tone = 'info',
  onDismiss,
  state = 'ready',
}: ToastProps): JSX.Element {
  // The six states have their own tones and one of them is `danger`. A toast
  // in its error state is a toast that could not be rendered, which is still
  // not an error the user has to act on -- so it reads as a warning here
  // rather than reintroducing the tone the type excludes.
  const stateTone = STATE_TONE[state];
  const resolvedTone: ToastTone =
    state === 'ready'
      ? tone
      : stateTone === 'danger' || stateTone === undefined
        ? 'warning'
        : stateTone;
  const shownTitle = state === 'ready' ? title : STATE_LABEL[state];

  return (
    <div className="jg-toast" data-jg-tone={resolvedTone} role="status" aria-live="polite">
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
          reserve="md"
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
