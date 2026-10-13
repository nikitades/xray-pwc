import pytest

from xray_pwc_probe.domain.release import Release


@pytest.mark.parametrize(
    "version", ["1.1.0", "v1.1.0", "1.1.0-dev.3f9c2ab41d0e", "1.1.0.dev0+3f9c2ab41d0e"]
)
def test_dev_builds_belong_to_their_release(version: str):
    assert Release.of(version) == Release(1, 1, 0)


def test_patch_versions_differ():
    assert Release.of("1.1.1") != Release.of("1.1.0")


@pytest.mark.parametrize("version", ["", "hello", "1.1", "dev-1.1.0"])
def test_rejects_what_is_not_a_version(version: str):
    with pytest.raises(ValueError, match="not a version"):
        Release.of(version)
