import type { JSX } from 'react';
import { Badge, DiffViewer, StateSurface, Table, type DiffLine } from '@draupnir/jarngreipr';
import { useResource } from '../api/useResource';
import { PageHeading } from './parts';

/**
 * S24 Policy and S25 Users and roles.
 *
 * Both screens render a table that is *enforced* elsewhere rather than a copy
 * of one. The policy is the `Policy` object the licence gate decides with; the
 * route table is generated from the same `@needs` declarations the guard reads
 * at request time. Neither can drift from what actually happens, because
 * neither is written twice.
 */

interface PolicyRule {
  id: string;
  statement: string;
  verdict: string;
  licences?: string[];
  personalData?: boolean | null;
  attributionRequired?: boolean | null;
}

interface Bundle {
  version: string;
  rules: PolicyRule[];
  defaultVerdict: string;
  defaultStatement: string;
}

/**
 * S24. The licence policy, and what changed since the last version.
 *
 * A version on its own tells an operator what the rules are and not what
 * moved, so the previous bundle is rendered beside it as a diff. That is the
 * question somebody actually arrives with: a source was permitted last month
 * and is refused now, and they want to know which clause did it.
 */
export function PolicyScreen(): JSX.Element {
  const policy = useResource('getPolicy', {});
  const data = policy.data;

  const columns = [
    { key: 'id', header: 'Rule', render: (row: PolicyRule) => <code>{row.id}</code> },
    {
      key: 'verdict',
      header: 'Verdict',
      render: (row: PolicyRule) => (
        <Badge
          tone={
            row.verdict === 'refuse'
              ? 'danger'
              : row.verdict === 'requires_approval'
                ? 'warning'
                : 'success'
          }
        >
          {row.verdict.replace('_', ' ')}
        </Badge>
      ),
    },
    { key: 'statement', header: 'Statement', render: (row: PolicyRule) => row.statement },
    {
      key: 'applies',
      header: 'Applies to',
      render: (row: PolicyRule) => appliesTo(row),
    },
  ];

  return (
    <>
      <PageHeading
        title="Policy"
        subtitle={data?.current.version ?? 'The licence policy in force'}
      />

      <StateSurface state={policy.state} problem={policy.problem} label="Policy" minHeight="16rem">
        {data === undefined ? null : (
          <>
            <p className="cn-note">
              First match wins, and a subject no rule matches is{' '}
              <strong>{data.current.defaultVerdict}</strong>. {data.current.defaultStatement}
            </p>

            <Table
              caption={`Rules of ${data.current.version}, in match order`}
              columns={columns}
              rows={data.current.rules}
              rowKey={(row) => row.id}
              state="ready"
            />

            {data.previous == null ? null : (
              <section aria-labelledby="cn-policy-diff-heading" data-testid="policy-diff">
                <h2 id="cn-policy-diff-heading">What changed</h2>
                <p className="cn-note">
                  Every decision records the version it was made under, so a source permitted under
                  one bundle and refused under the next is not a contradiction — it is two decisions
                  under two policies, and this is the difference between them.
                </p>
                <DiffViewer
                  fromLabel={data.previous.version}
                  toLabel={data.current.version}
                  lines={diffOf(data.previous, data.current)}
                />
              </section>
            )}
          </>
        )}
      </StateSurface>
    </>
  );
}

function appliesTo(rule: PolicyRule): string {
  const parts: string[] = [];
  if ((rule.licences ?? []).length > 0) parts.push((rule.licences ?? []).join(', '));
  if (rule.personalData === true) parts.push('personal data declared');
  if (rule.personalData === false) parts.push('no personal data');
  if (rule.attributionRequired === true) parts.push('attribution required');
  return parts.length === 0 ? 'any licence' : parts.join('; ');
}

/** A unified diff of two policy bundles, by rule identifier. */
function diffOf(previous: Bundle, current: Bundle): DiffLine[] {
  const before = new Map(previous.rules.map((rule) => [rule.id, rule]));
  const after = new Map(current.rules.map((rule) => [rule.id, rule]));
  const ids = [...new Set([...before.keys(), ...after.keys()])].sort();

  const lines: DiffLine[] = [
    { op: 'hunk', text: `@@ ${previous.version} → ${current.version} @@` },
  ];
  let oldNumber = 0;
  let newNumber = 0;

  for (const id of ids) {
    const was = before.get(id);
    const now = after.get(id);
    if (was !== undefined && now === undefined) {
      oldNumber += 1;
      lines.push({ op: 'remove', oldNumber, text: `${id}: ${was.verdict} — ${was.statement}` });
    } else if (was === undefined && now !== undefined) {
      newNumber += 1;
      lines.push({ op: 'add', newNumber, text: `${id}: ${now.verdict} — ${now.statement}` });
    } else if (was !== undefined && now !== undefined) {
      oldNumber += 1;
      newNumber += 1;
      if (was.verdict === now.verdict && was.statement === now.statement) {
        lines.push({ op: 'context', oldNumber, newNumber, text: `${id}: ${now.verdict}` });
      } else {
        lines.push({ op: 'remove', oldNumber, text: `${id}: ${was.verdict} — ${was.statement}` });
        lines.push({ op: 'add', newNumber, text: `${id}: ${now.verdict} — ${now.statement}` });
      }
    }
  }
  return lines;
}

interface Role {
  role: string;
  permissions: string[];
}

interface RoutePermission {
  method: string;
  path: string;
  permission?: string | null;
  reason?: string | null;
}

/**
 * S25. Roles, permissions, and what every route requires.
 *
 * The separation of duty is stated at the top rather than left to be inferred
 * from the matrix. Decision S6 is a single sentence — no role both submits and
 * approves — and a reader who has to derive it by comparing two columns is a
 * reader who will not notice when it stops being true.
 */
export function RolesScreen(): JSX.Element {
  const roles = useResource('getRoles', {});
  const data = roles.data;

  const roleColumns = [
    { key: 'role', header: 'Role', render: (row: Role) => <strong>{row.role}</strong> },
    {
      key: 'permissions',
      header: 'Permissions',
      render: (row: Role) => (
        <span className="cn-permissions">
          {row.permissions.map((permission) => (
            <Badge key={permission} tone="neutral">
              {permission}
            </Badge>
          ))}
        </span>
      ),
    },
  ];

  const routeColumns = [
    { key: 'method', header: 'Method', render: (row: RoutePermission) => row.method },
    { key: 'path', header: 'Route', render: (row: RoutePermission) => <code>{row.path}</code> },
    {
      key: 'permission',
      header: 'Requires',
      render: (row: RoutePermission) =>
        row.permission == null ? (
          <Badge tone="warning">
            unauthenticated
            <span className="jg-sr-only">. {row.reason ?? 'no reason recorded'}</span>
          </Badge>
        ) : (
          <Badge tone="info">{row.permission}</Badge>
        ),
    },
    {
      key: 'reason',
      header: 'Why',
      render: (row: RoutePermission) => row.reason ?? '',
    },
  ];

  return (
    <>
      <PageHeading title="Roles" subtitle="What each role may do, and what each route requires." />

      <StateSurface state={roles.state} problem={roles.problem} label="Roles" minHeight="16rem">
        {data === undefined ? null : (
          <>
            <p className="cn-separation" data-testid="separation-of-duty">
              {data.separation}
            </p>

            <Table
              caption="Roles and their permissions"
              columns={roleColumns}
              rows={data.roles}
              rowKey={(row) => row.role}
              state="ready"
            />

            <section aria-labelledby="cn-routes-heading">
              <h2 id="cn-routes-heading">Routes</h2>
              <p className="cn-note">
                Generated from the declarations the guard enforces, not assembled by hand. A
                published table written separately would disagree with the enforced rule the first
                time somebody added an endpoint.
              </p>
              <Table
                caption="Every route and what it requires"
                columns={routeColumns}
                rows={data.routes}
                rowKey={(row) => `${row.method} ${row.path}`}
                state="ready"
              />
            </section>
          </>
        )}
      </StateSurface>
    </>
  );
}
