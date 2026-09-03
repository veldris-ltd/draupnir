import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  ApiError,
  call,
  type CallOptions,
  type OperationId,
  type ResponseOf,
} from '@draupnir/api-client';
import type { ComponentState, ProblemSummary } from '@draupnir/jarngreipr';

/**
 * One read, in the vocabulary the design system already speaks.
 *
 * Every JARNGREIPR component takes a `ComponentState`, so a hook that returned
 * `{ data, loading, error }` would make every screen translate three booleans
 * into one state, twenty-nine times, slightly differently. This does the
 * translation once and returns the state itself.
 *
 * The mapping is where the six states earn their keep:
 *
 *   403 with no rows          -> `denied`, not `empty`. An operator who reads a
 *                                denial as "nothing here" draws the one wrong
 *                                conclusion available (AC-U13's sibling).
 *   409 `partitioned`         -> `partitioned`, not `error`. Training continues
 *                                and release does not (Decision S8); rendering
 *                                it as a failure sends someone to investigate a
 *                                network they cannot fix (AC-U12).
 *   an empty collection       -> `empty`, which is a fact rather than a fault.
 *   anything else non-2xx     -> `error`, with the problem document intact so
 *                                the surface can show the title, the action and
 *                                a copyable correlation id (AC-U14).
 *
 * No optimistic updates anywhere (UX 11): a value is shown when the API has
 * confirmed it. An interface that shows a state and then takes it back is
 * worse than one that waits.
 */

export interface Resource<T> {
  state: ComponentState;
  data: T | undefined;
  problem: ProblemSummary | undefined;
  /** Re-read. Used after a write, since nothing here is optimistic. */
  refresh: () => void;
}

export interface UseResourceOptions extends CallOptions {
  /** Skip the read entirely, for a screen that has not chosen a subject yet. */
  enabled?: boolean;
  /** Treat a zero-length collection at this key as `empty`. */
  emptyWhen?: (data: unknown) => boolean;
  /** Render `readOnly` instead of `ready` — the caller's role, not the API's. */
  readOnly?: boolean;
}

export function problemOf(error: ApiError): ProblemSummary {
  return {
    title: error.problem.title,
    detail: error.problem.detail,
    code: error.problem.code,
    correlationId: error.correlationId,
  };
}

/** Which of the six states an API failure means. */
export function stateForError(error: ApiError): ComponentState {
  if (error.status === 403) return 'denied';
  if (error.problem.code === 'site-partitioned' || error.problem.code === 'partitioned') {
    return 'partitioned';
  }
  return 'error';
}

export function useResource<Id extends OperationId>(
  id: Id,
  options: UseResourceOptions = {},
): Resource<ResponseOf<Id>> {
  const { enabled = true, emptyWhen, readOnly = false, ...callOptions } = options;
  const [data, setData] = useState<ResponseOf<Id> | undefined>(undefined);
  const [state, setState] = useState<ComponentState>(enabled ? 'loading' : 'empty');
  const [problem, setProblem] = useState<ProblemSummary | undefined>(undefined);
  const [nonce, setNonce] = useState(0);

  // The options object is rebuilt on every render by every caller; keying the
  // effect on its identity would re-read forever. Keyed on its content.
  const key = useMemo(() => JSON.stringify(callOptions), [callOptions]);
  const latest = useRef(0);

  useEffect(() => {
    if (!enabled) {
      setState('empty');
      return;
    }
    const controller = new AbortController();
    const attempt = ++latest.current;
    setState('loading');
    setProblem(undefined);

    call(id, { ...(JSON.parse(key) as CallOptions), signal: controller.signal })
      .then((result) => {
        if (attempt !== latest.current) return;
        setData(result.data);
        const empty = emptyWhen ? emptyWhen(result.data) : false;
        setState(empty ? 'empty' : readOnly ? 'readOnly' : 'ready');
      })
      .catch((cause: unknown) => {
        if (attempt !== latest.current || controller.signal.aborted) return;
        if (cause instanceof ApiError) {
          setProblem(problemOf(cause));
          setState(stateForError(cause));
          return;
        }
        // A transport failure is not a problem document, but the surface still
        // owes the operator a title and an action (AC-U14).
        setProblem({
          title: 'The console could not reach the API',
          detail:
            cause instanceof Error
              ? cause.message
              : 'The request did not complete and gave no reason.',
        });
        setState('error');
      });

    return () => {
      controller.abort();
    };
  }, [id, key, enabled, nonce, emptyWhen, readOnly]);

  const refresh = useCallback(() => {
    setNonce((value) => value + 1);
  }, []);

  return { state, data, problem, refresh };
}

/** `emptyWhen` for a paged collection. */
export function pageIsEmpty(data: unknown): boolean {
  return (
    data !== null &&
    typeof data === 'object' &&
    'items' in data &&
    Array.isArray((data as { items: unknown[] }).items) &&
    (data as { items: unknown[] }).items.length === 0
  );
}
