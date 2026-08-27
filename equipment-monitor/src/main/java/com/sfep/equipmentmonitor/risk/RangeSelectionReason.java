package com.sfep.equipmentmonitor.risk;

/** Explains whether an exact context or a conservative fallback supplied the range. */
public enum RangeSelectionReason {
    EXACT_CONTEXT,
    BAND_BOUNDARY_NOT_SEALED_FALLBACK,
    BAND_BOUNDARY_NOT_SEALED,
    NO_BASELINE,
    UNREGISTERED_EQUIPMENT,
    UNREGISTERED_CONTEXT,
    DATA_MISSING
}
