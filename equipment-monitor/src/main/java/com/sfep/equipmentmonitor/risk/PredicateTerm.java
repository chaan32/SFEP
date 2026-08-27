package com.sfep.equipmentmonitor.risk;

import java.util.List;

public record PredicateTerm(
        String field,
        PredicateType type,
        Double lower,
        Boolean lowerInclusive,
        Double upper,
        Boolean upperInclusive,
        List<RiskScalar> values) {
    public PredicateTerm {
        values = values == null ? null : List.copyOf(values);
    }
}
