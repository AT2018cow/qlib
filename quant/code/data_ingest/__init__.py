"""Daily A-share provider overlay for the Qlib production snapshot.

The package is deliberately outside ``qlib`` so upstream Qlib remains clean.
It implements the fail-closed path used when the legacy chenditc GitHub
release is stale:

- Sina daily raw/hfq as the primary incremental source;
- BaoStock as fallback and as the exact preclose/ST/suspension check;
- CSIndex official constituent files as the point-in-time universe;
- an append-only Qlib binary overlay rooted at the frozen history.

Nothing in this package publishes a signal or mutates the production volume.
"""

from __future__ import annotations

__all__ = [
    "calendar",
    "csindex",
    "gate",
    "manifest",
    "model",
    "overlay",
    "pipeline",
    "sina",
    "symbols",
]
