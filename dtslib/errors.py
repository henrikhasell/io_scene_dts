"""Exception types for the DTS format library."""


class DtsError(Exception):
    """Base class for all DTS format errors."""


class DtsUnsupportedVersion(DtsError):
    """File version outside the supported range (DTS/DSQ 17-24)."""

    def __init__(self, version, kind="DTS"):
        self.version = version
        self.kind = kind
        super().__init__(
            f"unsupported {kind} version {version} "
            f"(supported: 17-24)"
        )


class DtsGuardMismatch(DtsError):
    """A guard checkpoint in the three-buffer memory block did not match."""

    def __init__(self, expected, got32, got16, got8, offsets):
        self.expected = expected
        super().__init__(
            f"guard mismatch: expected {expected}, "
            f"got 32:{got32} 16:{got16} 8:{got8} "
            f"at byte offsets 32:{offsets[0]} 16:{offsets[1]} 8:{offsets[2]}"
        )


class DtsWriteError(DtsError):
    """The shape cannot be written in the requested version."""
