import type { JSX, KeyboardEvent as ReactKeyboardEvent } from 'react';
import { useEffect, useMemo, useRef, useState } from 'react';
import { ApiError, call } from '@draupnir/api-client';
import { navigate } from '../routing';

/**
 * The command palette. AC-U17.
 *
 * "The command palette covers navigation, run submission and search, and the
 * console is fully operable without a mouse."
 *
 * Three kinds of entry, and the third is why this is not just a navigation
 * menu: static commands (go to the board, compose a run), and live search
 * results from `GET /v1/search` across runs, sources and ledger entries at the
 * current site.
 *
 * The keyboard contract is the ARIA combobox pattern rather than an invention:
 * the input keeps focus throughout and `aria-activedescendant` moves the
 * selection, so a screen reader announces the highlighted option without the
 * focus ring leaving the field. Implementing this by moving DOM focus to each
 * option is the common alternative and it makes type-ahead impossible.
 */

export interface Command {
  id: string;
  label: string;
  detail: string;
  run: () => void;
}

const STATIC_COMMANDS: readonly Omit<Command, 'run'>[] = [
  { id: 'nav-overview', label: 'Go to Overview', detail: 'Site health at a glance' },
  { id: 'nav-runs', label: 'Go to Runs', detail: 'The run board' },
  { id: 'nav-compose', label: 'Submit a run', detail: 'Compose a specification and dry run it' },
  { id: 'nav-corpora', label: 'Go to Corpora', detail: 'Sources and licences' },
  { id: 'nav-models', label: 'Go to Models', detail: 'The model registry' },
  { id: 'nav-gates', label: 'Go to Gates', detail: 'The approval queue' },
  { id: 'nav-audit', label: 'Go to Audit', detail: 'The ledger explorer' },
  { id: 'nav-sites', label: 'Go to Sites', detail: 'The Forge Matrix' },
];

const DESTINATIONS: Record<string, string> = {
  'nav-overview': '/',
  'nav-runs': '/runs',
  'nav-compose': '/runs/compose',
  'nav-corpora': '/corpora',
  'nav-models': '/models',
  'nav-gates': '/gates',
  'nav-audit': '/audit',
  'nav-sites': '/sites',
};

interface Hit {
  kind: string;
  id: string;
  label: string;
  detail: string;
}

export interface CommandPaletteProps {
  open: boolean;
  onClose: () => void;
}

export function CommandPalette({ open, onClose }: CommandPaletteProps): JSX.Element | null {
  const [query, setQuery] = useState('');
  const [hits, setHits] = useState<Hit[]>([]);
  const [active, setActive] = useState(0);
  const [searchProblem, setSearchProblem] = useState<string | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const restoreTo = useRef<HTMLElement | null>(null);

  useEffect(() => {
    if (!open) return;
    // Remember what had focus so it can be given back. A dialog that returns
    // focus to the document body drops a keyboard user at the top of the page.
    restoreTo.current = document.activeElement as HTMLElement | null;
    inputRef.current?.focus();
    return () => {
      restoreTo.current?.focus();
    };
  }, [open]);

  useEffect(() => {
    if (!open || query.trim().length < 2) {
      setHits([]);
      setSearchProblem(null);
      return;
    }
    const controller = new AbortController();
    const timer = window.setTimeout(() => {
      call('search', { query: { q: query.trim(), limit: 8 }, signal: controller.signal })
        .then((result) => {
          setHits(result.data.items);
          setSearchProblem(null);
        })
        .catch((cause: unknown) => {
          if (controller.signal.aborted) return;
          // Stated, not swallowed. A palette that silently returns nothing
          // when search is down looks like a palette that found nothing.
          setSearchProblem(
            cause instanceof ApiError
              ? cause.problem.title
              : 'Search could not be reached. Navigation commands below still work.',
          );
          setHits([]);
        });
    }, 150);
    return () => {
      controller.abort();
      window.clearTimeout(timer);
    };
  }, [open, query]);

  const commands = useMemo<Command[]>(() => {
    const needle = query.trim().toLowerCase();
    const statics = STATIC_COMMANDS.filter(
      (command) =>
        needle === '' ||
        command.label.toLowerCase().includes(needle) ||
        command.detail.toLowerCase().includes(needle),
    ).map((command) => ({
      ...command,
      run: () => {
        navigate(DESTINATIONS[command.id] ?? '/');
      },
    }));

    const found = hits.map((hit) => ({
      id: `${hit.kind}:${hit.id}`,
      label: hit.label,
      detail: `${hit.kind} — ${hit.detail}`,
      run: () => {
        navigate(destinationFor(hit));
      },
    }));

    return [...statics, ...found];
  }, [query, hits]);

  useEffect(() => {
    setActive(0);
  }, [query]);

  if (!open) return null;

  const selected = commands[Math.min(active, commands.length - 1)];

  function onKeyDown(event: ReactKeyboardEvent<HTMLDivElement>): void {
    if (event.key === 'Escape') {
      onClose();
      event.preventDefault();
      return;
    }
    if (event.key === 'ArrowDown') {
      setActive((index) => (commands.length === 0 ? 0 : (index + 1) % commands.length));
      event.preventDefault();
      return;
    }
    if (event.key === 'ArrowUp') {
      setActive((index) =>
        commands.length === 0 ? 0 : (index - 1 + commands.length) % commands.length,
      );
      event.preventDefault();
      return;
    }
    if (event.key === 'Enter' && selected) {
      selected.run();
      onClose();
      event.preventDefault();
    }
  }

  return (
    <div className="cn-palette__scrim" role="presentation" onMouseDown={onClose}>
      {/* eslint-disable-next-line jsx-a11y/no-noninteractive-element-interactions */}
      <div
        className="cn-palette"
        role="dialog"
        aria-modal="true"
        aria-label="Command palette"
        onMouseDown={(event) => {
          event.stopPropagation();
        }}
        onKeyDown={onKeyDown}
      >
        <label className="jg-sr-only" htmlFor="cn-palette-input">
          Search runs, sources and ledger entries, or type a command
        </label>
        <input
          id="cn-palette-input"
          ref={inputRef}
          className="cn-palette__input"
          type="text"
          role="combobox"
          aria-expanded
          aria-controls="cn-palette-list"
          aria-activedescendant={selected ? `cn-palette-option-${selected.id}` : undefined}
          aria-autocomplete="list"
          placeholder="Search or type a command…"
          value={query}
          onChange={(event) => {
            setQuery(event.target.value);
          }}
        />
        {searchProblem === null ? null : (
          <p className="cn-palette__problem" role="status">
            {searchProblem}
          </p>
        )}
        <ul className="cn-palette__list" id="cn-palette-list" role="listbox" aria-label="Commands">
          {commands.length === 0 ? (
            <li className="cn-palette__empty">No command or result matches that.</li>
          ) : (
            commands.map((command, index) => (
              <li
                key={command.id}
                id={`cn-palette-option-${command.id}`}
                role="option"
                aria-selected={index === active}
                className="cn-palette__option"
                data-jg-active={index === active ? 'true' : undefined}
              >
                <button
                  type="button"
                  className="cn-palette__button"
                  tabIndex={-1}
                  onClick={() => {
                    command.run();
                    onClose();
                  }}
                >
                  <span className="cn-palette__label">{command.label}</span>
                  <span className="cn-palette__detail">{command.detail}</span>
                </button>
              </li>
            ))
          )}
        </ul>
        <p className="cn-palette__hint">
          Arrow keys to move, Enter to run, Escape to close. Every command here is also reachable by
          Tab.
        </p>
      </div>
    </div>
  );
}

function destinationFor(hit: Hit): string {
  switch (hit.kind) {
    case 'run':
      return `/runs/${hit.id}`;
    case 'source':
      return `/corpora?source=${encodeURIComponent(hit.id)}`;
    case 'ledger':
      return `/audit?entry=${encodeURIComponent(hit.id)}`;
    default:
      return '/';
  }
}

/** The platform shortcut: `⌘K` on Apple keyboards, `Ctrl+K` elsewhere. */
export function usePaletteShortcut(onOpen: () => void): void {
  useEffect(() => {
    const handler = (event: KeyboardEvent): void => {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === 'k') {
        onOpen();
        event.preventDefault();
      }
    };
    window.addEventListener('keydown', handler);
    return () => {
      window.removeEventListener('keydown', handler);
    };
  }, [onOpen]);
}
