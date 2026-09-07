package com.sfep.equipmentmonitor.risk;

import java.math.BigDecimal;
import java.util.Objects;

public final class RiskScalar {
    private final Object value;

    private RiskScalar(Object value) {
        this.value = value;
    }

    public static RiskScalar of(Object value) {
        Objects.requireNonNull(value, "value");
        if (value instanceof RiskScalar scalar) return scalar;
        if (value instanceof String || value instanceof Boolean) return new RiskScalar(value);
        if (value instanceof Number number) {
            return new RiskScalar(new BigDecimal(number.toString()).stripTrailingZeros());
        }
        throw new IllegalArgumentException("Risk scalar must be string, number, or boolean");
    }

    public Object value() {
        return value;
    }

    public boolean isNumber() {
        return value instanceof BigDecimal;
    }

    public double doubleValue() {
        if (!(value instanceof BigDecimal number)) throw new IllegalStateException("Not numeric");
        return number.doubleValue();
    }

    public boolean sameValue(RiskScalar other) {
        if (value instanceof BigDecimal left && other.value instanceof BigDecimal right) {
            return left.compareTo(right) == 0;
        }
        return value.equals(other.value);
    }

    @Override
    public boolean equals(Object other) {
        return other instanceof RiskScalar scalar && sameValue(scalar);
    }

    @Override
    public int hashCode() {
        return value instanceof BigDecimal decimal ? decimal.stripTrailingZeros().hashCode() : value.hashCode();
    }

    @Override
    public String toString() {
        return value.toString();
    }
}
