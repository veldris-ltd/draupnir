/**
 * Density: comfortable or compact. Section 4.5.
 *
 * The same shape as the theme provider and for the same reason: one attribute
 * on the document, and the cascade does the rest. Switching density changes no
 * component code, which is the exit condition of prompt UX-1.
 *
 * Compact is for CON-A and CON-B at 1280 x 720, and both of those are read
 * only. It reduces row height, body size and gutter. It does not reduce the
 * touch target, and it cannot: `--jg-touch-target` is declared once in
 * `density.css`, outside both mode blocks, so there is no place for a mode to
 * shrink it. AC-V7's test reads the file and fails if a mode declares it.
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

import { DENSITIES, DENSITY_ATTRIBUTE, type Density } from '../tokens';

const STORAGE_PREFIX = 'jarngreipr.density';

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

export interface DensityContextValue {
  density: Density;
  setDensity: (density: Density) => void;
  user?: string | undefined;
}

const DensityContext = createContext<DensityContextValue | null>(null);

export interface DensityProviderProps {
  children: ReactNode;
  /** The signed-in subject. The choice is stored against it. */
  user?: string | undefined;
  defaultDensity?: Density;
  target?: HTMLElement | null | undefined;
}

function storageKey(user: string | undefined): string {
  return user === undefined || user === '' ? STORAGE_PREFIX : `${STORAGE_PREFIX}:${user}`;
}

function isDensity(value: unknown): value is Density {
  return typeof value === 'string' && (DENSITIES as readonly string[]).includes(value);
}

export function readStoredDensity(user?: string): Density | undefined {
  try {
    const stored = storage()?.getItem(storageKey(user));
    return isDensity(stored) ? stored : undefined;
  } catch {
    return undefined;
  }
}

function writeStoredDensity(user: string | undefined, density: Density): void {
  try {
    storage()?.setItem(storageKey(user), density);
  } catch {
    // As with the theme: a preference that cannot be stored still works for
    // this session, and is not worth failing a render over.
  }
}

export function DensityProvider({
  children,
  user,
  defaultDensity = 'comfortable',
  target,
}: DensityProviderProps): JSX.Element {
  const [density, setDensityState] = useState<Density>(
    () => readStoredDensity(user) ?? defaultDensity,
  );

  useEffect(() => {
    setDensityState(readStoredDensity(user) ?? defaultDensity);
  }, [user, defaultDensity]);

  useEffect(() => {
    const element = target ?? root();
    if (!element) return;
    element.setAttribute(DENSITY_ATTRIBUTE, density);
  }, [density, target]);

  const setDensity = useCallback(
    (next: Density) => {
      setDensityState(next);
      writeStoredDensity(user, next);
    },
    [user],
  );

  const value = useMemo<DensityContextValue>(
    () => ({ density, setDensity, user }),
    [density, setDensity, user],
  );

  return <DensityContext.Provider value={value}>{children}</DensityContext.Provider>;
}

export function useDensity(): DensityContextValue {
  const value = useContext(DensityContext);
  if (value === null) {
    throw new Error('useDensity was called outside a DensityProvider');
  }
  return value;
}
