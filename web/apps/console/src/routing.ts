import { useCallback, useEffect, useMemo, useState } from 'react';

/**
 * Routing, in about eighty lines and with no dependency.
 *
 * The requirement is narrow -- "Every screen has a URL that restores its
 * state, including filters and the selected site" (UX 11) -- and it is met by
 * the URL being the state rather than a mirror of it. A screen reads its
 * filters from the query string and writes them back, so a link an operator
 * pastes into a ticket restores what they were looking at, including which
 * site they were looking at it on.
 *
 * A router library would add a dependency, a bundle and a second source of
 * truth for the current location, and AC-U16 puts the initial bundle under
 * 300 KB gzipped. `history.pushState` and `popstate` are the whole mechanism.
 */

export interface Location {
  path: string;
  query: URLSearchParams;
}

function read(): Location {
  return {
    path: window.location.pathname === '' ? '/' : window.location.pathname,
    query: new URLSearchParams(window.location.search),
  };
}

const listeners = new Set<() => void>();

function announce(): void {
  for (const listener of listeners) listener();
}

/** Navigate, pushing a history entry. */
export function navigate(to: string, options: { replace?: boolean } = {}): void {
  if (options.replace === true) window.history.replaceState(null, '', to);
  else window.history.pushState(null, '', to);
  announce();
}

/** The current location, updating on navigation and on back and forward. */
export function useLocation(): Location {
  const [location, setLocation] = useState<Location>(read);

  useEffect(() => {
    const update = (): void => {
      setLocation(read());
    };
    listeners.add(update);
    window.addEventListener('popstate', update);
    return () => {
      listeners.delete(update);
      window.removeEventListener('popstate', update);
    };
  }, []);

  return location;
}

/**
 * One query parameter, read and written as component state.
 *
 * Writing replaces rather than pushes: a filter change is not a navigation,
 * and a back button that walks an operator through every keystroke of a search
 * box is a back button they stop using.
 */
export function useQueryParam(name: string, fallback = ''): [string, (value: string) => void] {
  const location = useLocation();
  const value = location.query.get(name) ?? fallback;

  const set = useCallback(
    (next: string) => {
      const query = new URLSearchParams(window.location.search);
      if (next === '' || next === fallback) query.delete(name);
      else query.set(name, next);
      const suffix = query.toString();
      navigate(`${window.location.pathname}${suffix ? `?${suffix}` : ''}`, { replace: true });
    },
    [name, fallback],
  );

  return [value, set];
}

/** A route: a path pattern and the parameters it binds. */
export interface Route {
  /** `/runs/:runId`. One level of `:name` segments, which is all this needs. */
  pattern: string;
  params: Record<string, string>;
}

/** Match the current path against `patterns`, returning the first that matches. */
export function useRoute(patterns: readonly string[]): Route | null {
  const location = useLocation();
  return useMemo(() => {
    for (const pattern of patterns) {
      const params = match(pattern, location.path);
      if (params !== null) return { pattern, params };
    }
    return null;
  }, [patterns, location.path]);
}

function match(pattern: string, path: string): Record<string, string> | null {
  const expected = pattern.split('/').filter(Boolean);
  const actual = path.split('/').filter(Boolean);
  if (expected.length !== actual.length) return null;

  const params: Record<string, string> = {};
  for (const [index, segment] of expected.entries()) {
    const value = actual[index];
    if (value === undefined) return null;
    if (segment.startsWith(':')) params[segment.slice(1)] = decodeURIComponent(value);
    else if (segment !== value) return null;
  }
  return params;
}

/**
 * A link that navigates without a full page load.
 *
 * A real `href` rather than a click handler on a span: middle click, copy link
 * address and open in new tab all have to work, and a keyboard user reaches a
 * link with Tab and follows it with Enter without anyone implementing that.
 */
export function linkProps(to: string): {
  href: string;
  onClick: (event: React.MouseEvent<HTMLAnchorElement>) => void;
} {
  return {
    href: to,
    onClick: (event) => {
      if (event.defaultPrevented || event.button !== 0) return;
      if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
      event.preventDefault();
      navigate(to);
    },
  };
}
