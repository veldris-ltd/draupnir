/**
 * Theme: light, dark, or follow the system. Persisted per user.
 *
 * Three choices and two outcomes. `system` is a choice in its own right rather
 * than the absence of one, because a user who has never chosen and a user who
 * has chosen to follow their operating system want the same thing today and
 * different things the moment they change their mind. Storing "system" lets a
 * user go back to it.
 *
 * **Per user.** The console knows who is signed in, and CON-A and CON-B are
 * shared appliances where two operators use the same browser profile. So the
 * key carries the subject: one person switching to dark does not switch it for
 * the next person at the same keyboard. With no subject -- Storybook, a test,
 * a signed-out shell -- the key falls back to a shared one, which is the
 * correct behaviour for a browser nobody has identified themselves to.
 *
 * **Applied to the document, never to a component.** The provider writes one
 * attribute on `<html>` and everything else follows from the cascade. That is
 * the whole reason the exit condition "switching theme changes no component
 * code" holds: there is nothing for a component to do.
 */

import type { JSX } from 'react';
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from 'react';

import { THEMES, THEME_ATTRIBUTE, type ResolvedTheme, type ThemeChoice } from '../tokens';

const STORAGE_PREFIX = 'jarngreipr.theme';
const DARK_QUERY = '(prefers-color-scheme: dark)';

/**
 * The three browser globals this needs, each behind a guard.
 *
 * TypeScript's DOM library declares all three as always present, which is true
 * of a browser and not of the environments this also runs in: a Vitest suite
 * without jsdom, a Storybook static build, a server render. Reading them
 * through these keeps the optional chaining honest rather than a chain the
 * type checker calls unnecessary.
 */
function storage(): Storage | undefined {
  return typeof localStorage === 'undefined' ? undefined : localStorage;
}

function root(): HTMLElement | undefined {
  return typeof document === 'undefined' ? undefined : document.documentElement;
}

function darkQuery(): MediaQueryList | undefined {
  return typeof matchMedia === 'undefined' ? undefined : matchMedia(DARK_QUERY);
}

export interface ThemeContextValue {
  /** What the user chose, including `system`. */
  choice: ThemeChoice;
  /** What that resolves to now. `system` becomes light or dark. */
  theme: ResolvedTheme;
  setChoice: (choice: ThemeChoice) => void;
  /** The subject the choice is stored against, or undefined for the shared key. */
  user?: string | undefined;
}

const ThemeContext = createContext<ThemeContextValue | null>(null);

export interface ThemeProviderProps {
  children: ReactNode;
  /** The signed-in subject. The choice is stored against it. */
  user?: string | undefined;
  /** What to use before storage has been read, and when it holds nothing. */
  defaultChoice?: ThemeChoice;
  /**
   * The element the attribute is written on. `document.documentElement` in a
   * browser; a container in a test or a Storybook frame that renders both
   * themes on one page.
   */
  target?: HTMLElement | null | undefined;
}

function storageKey(user: string | undefined): string {
  return user === undefined || user === '' ? STORAGE_PREFIX : `${STORAGE_PREFIX}:${user}`;
}

function isChoice(value: unknown): value is ThemeChoice {
  return typeof value === 'string' && (THEMES as readonly string[]).includes(value);
}

/**
 * Read the stored choice.
 *
 * Wrapped, because `localStorage` throws rather than returning null in a
 * browser configured to block site data, and a console that failed to render
 * because it could not remember a colour preference would be a worse console
 * than one that renders in light.
 */
export function readStoredChoice(user?: string): ThemeChoice | undefined {
  try {
    const stored = storage()?.getItem(storageKey(user));
    return isChoice(stored) ? stored : undefined;
  } catch {
    return undefined;
  }
}

function writeStoredChoice(user: string | undefined, choice: ThemeChoice): void {
  try {
    storage()?.setItem(storageKey(user), choice);
  } catch {
    // A preference that cannot be remembered is still a preference that works
    // for this session. Nothing here is worth failing a render over.
  }
}

function systemTheme(): ResolvedTheme {
  return darkQuery()?.matches === true ? 'dark' : 'light';
}

/** Resolve a choice against the system, without subscribing to it. */
export function resolveTheme(choice: ThemeChoice): ResolvedTheme {
  return choice === 'system' ? systemTheme() : choice;
}

export function ThemeProvider({
  children,
  user,
  defaultChoice = 'system',
  target,
}: ThemeProviderProps): JSX.Element {
  const [choice, setChoiceState] = useState<ThemeChoice>(
    () => readStoredChoice(user) ?? defaultChoice,
  );
  const [system, setSystem] = useState<ResolvedTheme>(systemTheme);

  // A user who switches their operating system to dark while the console is
  // open, and has chosen `system`, gets dark without a reload.
  useEffect(() => {
    const query = darkQuery();
    if (query === undefined) return undefined;
    const listen = (event: MediaQueryListEvent): void => {
      setSystem(event.matches ? 'dark' : 'light');
    };
    query.addEventListener('change', listen);
    return () => {
      query.removeEventListener('change', listen);
    };
  }, []);

  // Re-read when the signed-in subject changes: the next person at a shared
  // appliance gets their own choice rather than the last person's.
  useEffect(() => {
    setChoiceState(readStoredChoice(user) ?? defaultChoice);
  }, [user, defaultChoice]);

  const theme: ResolvedTheme = choice === 'system' ? system : choice;

  useEffect(() => {
    const element = target ?? root();
    if (!element) return;
    element.setAttribute(THEME_ATTRIBUTE, theme);
  }, [theme, target]);

  const setChoice = useCallback(
    (next: ThemeChoice) => {
      setChoiceState(next);
      writeStoredChoice(user, next);
    },
    [user],
  );

  const value = useMemo<ThemeContextValue>(
    () => ({ choice, theme, setChoice, user }),
    [choice, theme, setChoice, user],
  );

  return <ThemeContext.Provider value={value}>{children}</ThemeContext.Provider>;
}

/**
 * The current theme.
 *
 * Throws outside a provider rather than defaulting to light. A component that
 * silently renders in the wrong theme is harder to notice than one that does
 * not render, and the provider is one line at the root.
 */
export function useTheme(): ThemeContextValue {
  const value = useContext(ThemeContext);
  if (value === null) {
    throw new Error('useTheme was called outside a ThemeProvider');
  }
  return value;
}
