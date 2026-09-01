"""The installed control plane is ours, and the other `draupnir` is absent.

A public package called `draupnir` exists on PyPI -- an unrelated
bioinformatics tool, published first and still maintained. The name cannot be
claimed, so the control plane is distributed as `veldris-draupnir` and nothing
asks an index for `draupnir` by name. The import name is unchanged.

That is a configuration, and configurations drift. This is the check that
notices. It asserts the absence of a distribution named `draupnir` rather than
inspecting which distribution provides the import package, because the mapping
from import package to distribution is empty for an editable install and the
check would then pass by knowing nothing.
"""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, distribution

import pytest

#: Every first party distribution. The prefix is the rule: a distribution
#: without it is not ours, whatever its import name suggests.
OURS = (
    "veldris-draupnir",
    "veldris-draupnir-local-subprocess",
    "veldris-draupnir-targz-export",
)

#: Names that belong to somebody else, or would be ambiguous if they existed.
#: `draupnir` is taken on PyPI; the other two are free, and are listed so that
#: reintroducing them is a decision rather than an accident.
NOT_OURS = ("draupnir", "draupnir-local-subprocess", "draupnir-targz-export")


@pytest.mark.parametrize("name", OURS)
def test_every_first_party_distribution_is_installed_under_the_veldris_name(
    name: str,
) -> None:
    assert distribution(name).metadata["Name"] == name
    assert name.startswith("veldris-")


@pytest.mark.parametrize("name", NOT_OURS)
def test_no_unprefixed_distribution_is_installed(name: str) -> None:
    """The guard. `draupnir` on PyPI is a different project entirely.

    If this fails, something has resolved a first party name from a public
    index. Catching it here costs a millisecond; catching it in production
    means the control plane imported a variational autoencoder.
    """
    with pytest.raises(PackageNotFoundError):
        distribution(name)


def test_the_control_plane_still_imports_under_its_own_name() -> None:
    # The rename is of the distribution, not the package. Nothing in the
    # source changed, and this is what says so.
    import draupnir
    import draupnirctl

    assert draupnir.__version__
    assert draupnirctl.__doc__


def test_the_console_script_is_unaffected() -> None:
    scripts = distribution("veldris-draupnir").entry_points.select(group="console_scripts")
    assert [point.name for point in scripts] == ["draupnirctl"]
