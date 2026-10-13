import re

from attrs import frozen

_RELEASE = re.compile(r"v?(\d+)\.(\d+)\.(\d+)")


@frozen
class Release:
    """The X.Y.Z of a version: the probe works only with xray-pwc-connect of
    the same one. A dev build adds to it (1.1.0-dev.<hash> in Go,
    1.1.0.dev0+<hash> in Python) and still belongs to its release."""

    major: int
    minor: int
    patch: int

    @classmethod
    def of(cls, version: str) -> Release:
        match = _RELEASE.match(version.strip())
        if match is None:
            raise ValueError(f"{version!r} is not a version")
        major, minor, patch = map(int, match.groups())
        return cls(major, minor, patch)

    def __str__(self) -> str:
        return f"{self.major}.{self.minor}.{self.patch}"
