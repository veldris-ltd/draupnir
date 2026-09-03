import type { JSX } from 'react';
import { StateSurface } from '@draupnir/jarngreipr';
import { useResource } from './api/useResource';
import { useRoute } from './routing';
import { Shell } from './shell/Shell';
import { Overview, Sites } from './screens/Overview';
import { RegisterSource, SourceRegister } from './screens/Corpora';
import { ComposeRun, RunBoard, RunDetail } from './screens/Runs';
import { ApprovalDetail, GateQueue } from './screens/Gates';
import { LedgerExplorer, LineageExplorer, ModelRegistry } from './screens/Audit';
import { CurationRuns, RetentionSchedule } from './screens/Curation';
import { ArrayMonitor, Plugins, SweepComparison } from './screens/Operations';
import { PolicyScreen, RolesScreen } from './screens/Governance';
import {
  AttestationExport,
  LedgerEntryDetail,
  ModelDetail,
  ReleasePackage,
} from './screens/Release';
import { SignIn } from './screens/SignIn';
import { KioskDashboard } from './screens/Kiosk';
import './console.css';

/**
 * The console.
 *
 * Routes are listed most specific first, because the matcher returns the first
 * pattern that fits and `/runs/compose` would otherwise be read as a run whose
 * identifier is `compose`.
 */
const ROUTES = [
  '/runs/compose',
  '/runs/array',
  '/runs/:runId/sweep',
  '/runs/:runId',
  '/runs',
  '/corpora/register',
  '/corpora/curation',
  '/corpora/retention',
  '/corpora',
  '/models/:artefact/lineage',
  '/models/:artefact/release',
  '/models/:artefact/attestation',
  '/models/:artefact',
  '/models',
  '/gates/:gateId',
  '/gates',
  '/audit/:entryHash',
  '/audit',
  '/sites',
  '/admin/plugins',
  '/admin/policy',
  '/admin/roles',
  '/kiosk',
  '/signin',
  '/',
] as const;

export function App(): JSX.Element {
  const route = useRoute(ROUTES);
  const kiosk = route?.pattern === '/kiosk';
  // The site the console is talking to. From `/healthz`, which is
  // unauthenticated and answers before anything else can, so the shell can
  // state the site even when every other read is denied.
  const health = useResource('getHealth', {});
  const sites = useResource('listSites', {});

  const roles = ['operator'];

  // S31 renders outside the shell. It has no navigation, no site switcher and
  // no command palette, because nobody stands at it -- it is on a wall.
  if (kiosk) return <KioskDashboard />;

  return (
    <Shell siteId={health.data?.siteId ?? null} sites={sites.data?.items ?? []} roles={roles}>
      {render(route)}
    </Shell>
  );
}

function render(route: ReturnType<typeof useRoute>): JSX.Element {
  if (route === null) {
    return (
      <StateSurface
        state="empty"
        label="Page"
        stateMessage="There is no screen at this address. The navigation on the left lists every section."
      >
        <span />
      </StateSurface>
    );
  }

  switch (route.pattern) {
    case '/':
      return <Overview />;
    case '/runs':
      return <RunBoard />;
    case '/runs/compose':
      return <ComposeRun />;
    case '/runs/:runId':
      return <RunDetail runId={route.params.runId ?? ''} />;
    case '/corpora':
      return <SourceRegister />;
    case '/corpora/register':
      return <RegisterSource />;
    case '/models':
      return <ModelRegistry />;
    case '/models/:artefact':
      return <ModelDetail artefact={route.params.artefact ?? ''} />;
    case '/models/:artefact/lineage':
      return <LineageExplorer artefact={route.params.artefact ?? ''} />;
    case '/models/:artefact/release':
      return <ReleasePackage artefact={route.params.artefact ?? ''} />;
    case '/models/:artefact/attestation':
      return <AttestationExport artefact={route.params.artefact ?? ''} />;
    case '/runs/array':
      return <ArrayMonitor />;
    case '/runs/:runId/sweep':
      return <SweepComparison runId={route.params.runId ?? ''} />;
    case '/corpora/curation':
      return <CurationRuns />;
    case '/corpora/retention':
      return <RetentionSchedule />;
    case '/admin/plugins':
      return <Plugins />;
    case '/admin/policy':
      return <PolicyScreen />;
    case '/admin/roles':
      return <RolesScreen />;
    case '/signin':
      return <SignIn />;
    case '/gates':
      return <GateQueue />;
    case '/gates/:gateId':
      return <ApprovalDetail gateId={route.params.gateId ?? ''} />;
    case '/audit':
      return <LedgerExplorer />;
    case '/audit/:entryHash':
      return <LedgerEntryDetail entryHash={route.params.entryHash ?? ''} />;
    case '/sites':
      return <Sites />;
    default:
      return <Overview />;
  }
}
