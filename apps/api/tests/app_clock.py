"""
Run one module's app clock ahead of, or behind, the database's (D-170).

The database's clock and this machine's agree to within a second on staging,
so a clock-skew defect never shows up by itself in a test. This makes it show:
`module.datetime.now()` returns the real time plus `by`, while the database
keeps the real time. A module that follows the database's clock is unaffected;
one that decides by its own clock goes wrong by exactly `by`.

A module that no longer imports `datetime` at all (because it was fixed to
read the database's clock) is simply left alone -- `raising=False`.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from types import ModuleType

import pytest


def skew_app_clock(monkeypatch: pytest.MonkeyPatch, module: ModuleType, *, by: timedelta) -> None:
    real = datetime

    class _Skewed(real):
        @classmethod
        def now(cls, tz=None):  # type: ignore[override]
            return real.now(tz) + by

    monkeypatch.setattr(module, "datetime", _Skewed, raising=False)
