import type { JSX, ReactNode } from 'react';
import { useCallback, useEffect, useRef, useState } from 'react';
import { Badge, Button } from '@draupnir/jarngreipr';
import type { Site } from '@draupnir/api-client';
import { linkProps, useLocation } from '../routing';
import { CommandPalette, usePaletteShortcut } from './CommandPalette';

/**
 * The application shell.
 *
 * Four things live here because they are true of every screen rather than of
 * any one of them:
 *
 * **The site.** AC-U11: "Where more than one site is registered, every view
 * states which site it shows. No unscoped aggregate view exists." Putting the
 * site in the shell is what makes that structural. A screen cannot forget to
 * state its site, because no screen states it.
 *
 * **The anchor state.** A partitioned site is stated here, once, above
 * everything (AC-U12) -- not discovered when a publish returns 409. The
 * publish screen disables its control and repeats the reason; this is the
 * standing notice that the site is in that condition at all.
 *
 * **The skip link.** WCAG 2.4.1. First thing in the tab order, and the only
 * reason a keyboard user does not tab through the whole navigation on every
 * page.
 *
 * **The command palette.** AC-U17.
 */

export interface ShellProps {
  children: ReactNode;
  /** The site this console is talking to, once `/healthz` has answered. */
  siteId: string | null;
  /** Every registered site, for the switcher. Empty until `/v1/sites` answers. */
  sites: readonly Site[];
  /** The signed-in principal's roles, for the navigation. */
  roles: readonly string[];
}

/**
 * The seven sections of UX 7, "chosen to match the shape of the work rather
 * than the shape of the data model". Corpora, Runs, Models and Gates are the
 * four journeys; Overview, Admin and Audit support them.
 */
const SECTIONS = [
  { path: '/', label: 'Overview' },
  { path: '/corpora', label: 'Corpora' },
  { path: '/runs', label: 'Runs' },
  { path: '/models', label: 'Models' },
  { path: '/gates', label: 'Gates' },
  { path: '/audit', label: 'Audit' },
  { path: '/admin', label: 'Admin' },
] as const;

/** The screens under each section, shown when that section is current. */
const SUBSECTIONS: Record<string, readonly { path: string; label: string }[]> = {
  '/corpora': [
    { path: '/corpora', label: 'Sources' },
    { path: '/corpora/curation', label: 'Curation' },
    { path: '/corpora/retention', label: 'Retention' },
  ],
  '/runs': [
    { path: '/runs', label: 'Board' },
    { path: '/runs/array', label: 'Array' },
    { path: '/runs/compose', label: 'Compose' },
  ],
  '/admin': [
    { path: '/sites', label: 'Sites' },
    { path: '/admin/plugins', label: 'Plug-ins' },
    { path: '/admin/policy', label: 'Policy' },
    { path: '/admin/roles', label: 'Roles' },
  ],
};

export function Shell({ children, siteId, sites, roles }: ShellProps): JSX.Element {
  const [paletteOpen, setPaletteOpen] = useState(false);
  const location = useLocation();
  const main = useRef<HTMLElement>(null);

  const openPalette = useCallback(() => {
    setPaletteOpen(true);
  }, []);
  usePaletteShortcut(openPalette);

  // UX 11: "A navigation change moves focus to the new page heading." Without
  // this a keyboard user stays where the link was and a screen reader user is
  // told nothing happened.
  //
  // Keyed on the previous *path* rather than on a "have I run before" flag.
  // React's StrictMode mounts, unmounts and remounts every effect in
  // development, so a boolean guard is false on the second mount and focus is
  // yanked to the heading on first load -- which puts the skip link behind the
  // user before they have pressed anything.
  const previousPath = useRef(location.path);
  useEffect(() => {
    if (previousPath.current === location.path) return;
    previousPath.current = location.path;
    main.current?.querySelector<HTMLElement>('h1')?.focus();
  }, [location.path]);

  const here = sites.find((site) => site.id === siteId) ?? null;
  const partitioned = here !== null && here.anchorState === 'PARTITIONED';

  return (
    <div className="cn-shell">
      <a className="cn-skip" href="#cn-main">
        Skip to main content
      </a>

      <header className="cn-header">
        <div className="cn-header__brand">
          <span className="cn-header__mark" aria-hidden="true">
            ◎
          </span>
          <span className="cn-header__name">DRAUPNIR</span>
        </div>

        <SiteSwitcher siteId={siteId} sites={sites} />

        <div className="cn-header__tools">
          <Button variant="secondary" size="sm" onClick={openPalette}>
            Search or command
          </Button>
          <span className="cn-header__shortcut" aria-hidden="true">
            Ctrl K
          </span>
          <span className="jg-sr-only">
            Press Control K, or Command K on an Apple keyboard, to open the command palette.
          </span>
        </div>
      </header>

      {partitioned ? (
        <div className="cn-partition" role="status">
          <strong>{here.name} is partitioned from the federation.</strong> Training and evaluation
          continue. Release is unavailable until the link returns and the chain head is
          countersigned. This is a normal operating condition, not a fault to investigate.
        </div>
      ) : null}

      <div className="cn-body">
        <nav className="cn-nav" aria-label="Sections">
          <ul>
            {SECTIONS.map((section) => {
              const current =
                section.path === '/'
                  ? location.path === '/'
                  : location.path.startsWith(section.path) ||
                    (section.path === '/admin' && location.path === '/sites');
              const children = SUBSECTIONS[section.path];
              return (
                <li key={section.path}>
                  <a
                    {...linkProps(section.path === '/admin' ? '/sites' : section.path)}
                    aria-current={current ? 'page' : undefined}
                    className="cn-nav__link"
                  >
                    {section.label}
                  </a>
                  {current && children !== undefined ? (
                    <ul className="cn-nav__children" aria-label={`${section.label} screens`}>
                      {children.map((child) => (
                        <li key={child.path}>
                          <a
                            {...linkProps(child.path)}
                            aria-current={location.path === child.path ? 'page' : undefined}
                            className="cn-nav__link cn-nav__link--child"
                          >
                            {child.label}
                          </a>
                        </li>
                      ))}
                    </ul>
                  ) : null}
                </li>
              );
            })}
          </ul>
          <p className="cn-nav__roles">
            <span className="cn-nav__roles-label">Signed in as</span>{' '}
            {roles.length === 0 ? 'no role' : roles.join(', ')}
          </p>
        </nav>

        <main className="cn-main" id="cn-main" ref={main} tabIndex={-1}>
          {children}
        </main>
      </div>

      <CommandPalette
        open={paletteOpen}
        onClose={() => {
          setPaletteOpen(false);
        }}
      />
    </div>
  );
}

/**
 * The site switcher.
 *
 * Rendered only when more than one site is registered, exactly as AC-U11
 * phrases it. Switching changes which control plane the console talks to,
 * because the API resolves the site from the verified claim and refuses to
 * take it from the client -- "a site the caller can name is a site the caller
 * can change". So the switcher is a set of links to each site's own console,
 * not a filter applied to a shared one, and there is consequently no way to
 * assemble an aggregate view out of it.
 */
function SiteSwitcher({
  siteId,
  sites,
}: {
  siteId: string | null;
  sites: readonly Site[];
}): JSX.Element {
  const here = sites.find((site) => site.id === siteId) ?? null;

  if (sites.length <= 1) {
    return (
      <p className="cn-site" data-testid="site-context">
        <span className="cn-site__label">Site</span>
        <span className="cn-site__name">{here?.name ?? siteId ?? 'unknown'}</span>
        <AnchorBadge state={here?.anchorState} />
      </p>
    );
  }

  return (
    <div className="cn-site" data-testid="site-context">
      <span className="cn-site__label" id="cn-site-label">
        Site
      </span>
      <ul className="cn-site__list" aria-labelledby="cn-site-label">
        {sites.map((site) => (
          <li key={site.id}>
            <a
              className="cn-site__option"
              href={site.controlPlaneUri}
              aria-current={site.id === siteId ? 'true' : undefined}
              data-testid={`site-option-${site.id}`}
            >
              {site.name}
              <AnchorBadge state={site.anchorState} />
            </a>
          </li>
        ))}
      </ul>
    </div>
  );
}

function AnchorBadge({ state }: { state: string | undefined }): JSX.Element | null {
  if (state === undefined) return null;
  const tone = state === 'ANCHORED' ? 'success' : state === 'PARTITIONED' ? 'warning' : 'neutral';
  return (
    <Badge tone={tone}>
      <span className="jg-sr-only">Federation anchor state: </span>
      {state.toLowerCase()}
    </Badge>
  );
}
