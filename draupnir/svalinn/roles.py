"""Roles and permissions. SAD 9.4.

Five roles, and the table below is the whole of what each may do. It is data
rather than a set of `if` statements for the reason every other table in this
system is: a permission that lives in a branch somewhere cannot be audited, and
an auditor asking "who can publish" should get an answer by reading one thing.

Decision S6 is enforced structurally: `operator` may submit and `approver` may
publish, and no role holds both. That is checked by a test rather than trusted,
because the natural way it breaks is somebody adding a convenience role for a
small team.

Constraint C-11 is the standing exception. Release approval is held by one named
individual who also submits runs, so separation of duties is unavailable in
practice. The system does not resolve that by widening a role; it records
`sole_approver_exception` on the approval and surfaces it in the lineage and the
model card (SAD 9.4, AC-S15). The role model stays correct and the deviation
stays visible, which is the only arrangement that survives due diligence.
"""

from __future__ import annotations

from collections.abc import Iterable
from enum import StrEnum
from typing import Final


class Role(StrEnum):
    """The five roles of SAD 9.4."""

    VIEWER = "viewer"
    CURATOR = "curator"
    OPERATOR = "operator"
    APPROVER = "approver"
    ADMIN = "admin"


class Permission(StrEnum):
    """What a request is trying to do, in the vocabulary the roles are written in."""

    READ = "read"
    REGISTER_SOURCE = "register_source"
    CURATE = "curate"
    SUBMIT_RUN = "submit_run"
    CANCEL_RUN = "cancel_run"
    DECIDE_GATE = "decide_gate"
    PUBLISH_RELEASE = "publish_release"
    MANAGE_USERS = "manage_users"
    MANAGE_PLUGINS = "manage_plugins"
    MANAGE_POLICY = "manage_policy"
    #: Deliberately granted to nobody. Present so that an attempt to delete a
    #: ledger entry has a name and is refused by the same machinery as anything
    #: else, rather than being unreachable by accident.
    DELETE_LEDGER_ENTRY = "delete_ledger_entry"


#: SAD 9.4, transcribed. Every role reads; beyond that each row is the
#: permissions column of the table, and the "cannot" clauses are the absences.
GRANTS: Final[dict[Role, frozenset[Permission]]] = {
    Role.VIEWER: frozenset({Permission.READ}),
    Role.CURATOR: frozenset({Permission.READ, Permission.REGISTER_SOURCE, Permission.CURATE}),
    Role.OPERATOR: frozenset({Permission.READ, Permission.SUBMIT_RUN, Permission.CANCEL_RUN}),
    Role.APPROVER: frozenset({Permission.READ, Permission.DECIDE_GATE, Permission.PUBLISH_RELEASE}),
    Role.ADMIN: frozenset(
        {
            Permission.READ,
            Permission.MANAGE_USERS,
            Permission.MANAGE_PLUGINS,
            Permission.MANAGE_POLICY,
        }
    ),
}

#: The pair Decision S6 keeps apart. Named so the test that enforces it reads
#: as the decision rather than as two strings that happen to be compared.
SEPARATED: Final[tuple[Permission, Permission]] = (
    Permission.SUBMIT_RUN,
    Permission.PUBLISH_RELEASE,
)

#: Permissions no role holds. `DELETE_LEDGER_ENTRY` is here because SAD 9.4
#: says admin "cannot delete ledger entries" and the ledger is append only:
#: there is no role to add it to, and nothing should invent one.
UNGRANTED: Final[frozenset[Permission]] = frozenset(
    set(Permission) - {item for grants in GRANTS.values() for item in grants}
)


class RoleError(Exception):
    """Raised when a role or permission cannot be resolved."""


def parse(name: str) -> Role:
    """Return a role by name, or raise naming the five that exist."""
    try:
        return Role(name)
    except ValueError as error:
        known = ", ".join(str(item) for item in Role)
        msg = f"{name!r} is not a DRAUPNIR role; the roles are {known} (SAD 9.4)"
        raise RoleError(msg) from error


def permissions_of(roles: Iterable[Role | str]) -> frozenset[Permission]:
    """Every permission the union of these roles carries."""
    granted: set[Permission] = set()
    for item in roles:
        role = item if isinstance(item, Role) else parse(str(item))
        granted |= GRANTS[role]
    return frozenset(granted)


def allows(roles: Iterable[Role | str], permission: Permission) -> bool:
    """Whether these roles carry `permission`."""
    return permission in permissions_of(roles)


def roles_with(permission: Permission) -> tuple[Role, ...]:
    """Every role holding `permission`, for an audit answer."""
    return tuple(sorted(role for role, granted in GRANTS.items() if permission in granted))


def as_payload() -> dict[str, list[str]]:
    """The role table, for the console and the compliance evidence pack."""
    return {str(role): sorted(str(item) for item in granted) for role, granted in GRANTS.items()}
