"""Hand-authored fixtures for quality-risk statistics tests."""

from __future__ import annotations

import pandas as pd


def two_strata_fixture():
    """Return literal cells whose fixed-weight rates are easy to verify by hand."""
    from equipment_quality.statistics import Stratum

    return (
        Stratum(a=1, b=9, c=1, d=9, key="A"),
        Stratum(a=2, b=8, c=1, d=9, key="B"),
    )


def bootstrap_fixture_with_two_coils_per_charge() -> pd.DataFrame:
    """Eight literal Charge blocks with exactly two Coil rows per Charge."""
    rows = [
        {"charge_id": "C01", "stratum": "A", "candidate": True, "judge": "불량"},
        {"charge_id": "C01", "stratum": "A", "candidate": False, "judge": "양품"},
        {"charge_id": "C02", "stratum": "A", "candidate": True, "judge": "불량"},
        {"charge_id": "C02", "stratum": "A", "candidate": False, "judge": "양품"},
        {"charge_id": "C03", "stratum": "A", "candidate": True, "judge": "양품"},
        {"charge_id": "C03", "stratum": "A", "candidate": False, "judge": "불량"},
        {"charge_id": "C04", "stratum": "A", "candidate": True, "judge": "양품"},
        {"charge_id": "C04", "stratum": "A", "candidate": False, "judge": "양품"},
        {"charge_id": "C05", "stratum": "B", "candidate": True, "judge": "불량"},
        {"charge_id": "C05", "stratum": "B", "candidate": False, "judge": "불량"},
        {"charge_id": "C06", "stratum": "B", "candidate": True, "judge": "불량"},
        {"charge_id": "C06", "stratum": "B", "candidate": False, "judge": "양품"},
        {"charge_id": "C07", "stratum": "B", "candidate": True, "judge": "양품"},
        {"charge_id": "C07", "stratum": "B", "candidate": False, "judge": "불량"},
        {"charge_id": "C08", "stratum": "B", "candidate": True, "judge": "양품"},
        {"charge_id": "C08", "stratum": "B", "candidate": False, "judge": "양품"},
    ]
    return pd.DataFrame(rows, columns=("charge_id", "stratum", "candidate", "judge"))
