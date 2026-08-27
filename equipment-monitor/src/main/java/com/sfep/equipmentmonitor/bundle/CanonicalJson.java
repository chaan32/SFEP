package com.sfep.equipmentmonitor.bundle;

import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;

import java.math.BigInteger;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.List;

/** Canonical JSON encoder matching analysis/equipment_quality/deterministic.py. */
final class CanonicalJson {
    private static final ObjectMapper JSON = new ObjectMapper();

    private CanonicalJson() {
    }

    static byte[] encodeObject(JsonNode object) {
        if (object == null || !object.isObject()) {
            throw new IllegalArgumentException("canonical JSON input must be an object");
        }
        StringBuilder output = new StringBuilder();
        append(object, output);
        output.append('\n');
        return output.toString().getBytes(StandardCharsets.UTF_8);
    }

    private static void append(JsonNode node, StringBuilder output) {
        if (node == null || node.isNull()) {
            output.append("null");
        } else if (node.isBoolean()) {
            output.append(node.booleanValue() ? "true" : "false");
        } else if (node.isIntegralNumber()) {
            output.append(node.bigIntegerValue());
        } else if (node.isNumber()) {
            output.append(Binary64.format(node.doubleValue()));
        } else if (node.isTextual()) {
            appendString(node.textValue(), output);
        } else if (node.isArray()) {
            output.append('[');
            for (int index = 0; index < node.size(); index++) {
                if (index > 0) output.append(',');
                append(node.get(index), output);
            }
            output.append(']');
        } else if (node.isObject()) {
            List<String> names = new ArrayList<>();
            node.fieldNames().forEachRemaining(names::add);
            names.sort(Utf8Order.COMPARATOR);
            output.append('{');
            for (int index = 0; index < names.size(); index++) {
                if (index > 0) output.append(',');
                String name = names.get(index);
                appendString(name, output);
                output.append(':');
                append(node.get(name), output);
            }
            output.append('}');
        } else {
            throw new IllegalArgumentException("unsupported canonical JSON node: " + node.getNodeType());
        }
    }

    private static void appendString(String value, StringBuilder output) {
        try {
            output.append(JSON.writeValueAsString(value));
        } catch (JsonProcessingException error) {
            throw new IllegalArgumentException("cannot encode canonical JSON string", error);
        }
    }

    /** Exhaustive shortest round-trip decimal search with lexical tie-breaking. */
    private static final class Binary64 {
        private static final BigInteger TEN = BigInteger.TEN;
        private static final BigInteger[] POW10 = powersOfTen();

        private Binary64() {
        }

        private static String format(double number) {
            if (!Double.isFinite(number)) {
                throw new IllegalArgumentException("canonical JSON numbers must be finite");
            }
            if (number == 0.0d) {
                return "0";
            }

            String best = initialSpelling(number);
            boolean negative = number < 0.0d;
            double magnitude = negative ? -number : number;
            Fraction[] interval = roundingInterval(magnitude);
            String prefix = negative ? "-" : "";
            int maximumDigits = best.length() - prefix.length();

            for (int digitsCount = 1; digitsCount <= maximumDigits; digitsCount++) {
                int lowerOrder = decimalOrder(interval[0]);
                int upperOrder = decimalOrder(interval[1]);
                for (int exponent = lowerOrder - digitsCount - 1;
                     exponent < upperOrder - digitsCount + 2;
                     exponent++) {
                    if (minimumSpellingLength(digitsCount, exponent, prefix.length()) > best.length()) {
                        continue;
                    }
                    for (BigInteger coefficient : firstFeasibleCoefficients(
                            interval[0], interval[1], exponent, digitsCount)) {
                        if (coefficient.mod(TEN).signum() == 0) {
                            continue;
                        }
                        for (String spelling : decimalSpellings(
                                coefficient, exponent, best.length() - prefix.length())) {
                            String candidate = prefix + spelling;
                            if (betterOrEqual(candidate, best) && parsesAs(candidate, number)) {
                                best = candidate;
                            }
                        }
                    }
                }
            }
            return best;
        }

        private static Fraction[] roundingInterval(double number) {
            Fraction target = Fraction.fromDouble(number);
            Fraction previous = Fraction.fromDouble(Math.nextDown(number));
            Fraction lower = previous.add(target).half();
            double followingValue = Math.nextUp(number);
            Fraction upper = Double.isInfinite(followingValue)
                    ? target.add(target.subtract(previous).half())
                    : target.add(Fraction.fromDouble(followingValue)).half();
            return new Fraction[]{lower, upper};
        }

        private static String initialSpelling(double number) {
            boolean negative = number < 0.0d;
            double magnitude = negative ? -number : number;
            String representation = Double.toString(magnitude).toLowerCase(java.util.Locale.ROOT);
            int marker = representation.indexOf('e');
            String coefficient = marker < 0 ? representation : representation.substring(0, marker);
            int scientificExponent = marker < 0 ? 0 : Integer.parseInt(representation.substring(marker + 1));
            int dot = coefficient.indexOf('.');
            String integer = dot < 0 ? coefficient : coefficient.substring(0, dot);
            String fraction = dot < 0 ? "" : coefficient.substring(dot + 1);
            String digits = stripLeadingZeros(integer + fraction);
            int decimalExponent = scientificExponent - fraction.length();
            while (digits.endsWith("0")) {
                digits = digits.substring(0, digits.length() - 1);
                decimalExponent++;
            }
            String prefix = negative ? "-" : "";
            String best = null;
            for (String spelling : decimalSpellings(new BigInteger(digits), decimalExponent, 1000)) {
                String candidate = prefix + spelling;
                if (parsesAs(candidate, number) && (best == null || better(candidate, best))) {
                    best = candidate;
                }
            }
            if (best == null) {
                throw new IllegalStateException("no initial binary64 spelling for " + number);
            }
            return best;
        }

        private static List<String> decimalSpellings(BigInteger coefficient, int exponent, int limit) {
            String digits = coefficient.toString();
            List<String> spellings = new ArrayList<>();
            String fixed;
            if (exponent >= 0) {
                fixed = digits + "0".repeat(exponent);
            } else {
                int point = digits.length() + exponent;
                fixed = point > 0
                        ? digits.substring(0, point) + "." + digits.substring(point)
                        : "0." + "0".repeat(-point) + digits;
            }
            if (fixed.length() <= limit) {
                spellings.add(fixed);
            }
            for (int point = 1; point <= digits.length(); point++) {
                String mantissa = point == digits.length()
                        ? digits
                        : digits.substring(0, point) + "." + digits.substring(point);
                String scientific = mantissa + "e" + (exponent + digits.length() - point);
                if (scientific.length() <= limit) {
                    spellings.add(scientific);
                }
            }
            return spellings;
        }

        private static int minimumSpellingLength(int digitsCount, int exponent, int signLength) {
            int fixedLength;
            if (exponent >= 0) {
                fixedLength = digitsCount + exponent;
            } else {
                int point = digitsCount + exponent;
                fixedLength = point > 0 ? digitsCount + 1 : 2 + (-point) + digitsCount;
            }
            int scientificLength = Integer.MAX_VALUE;
            for (int point = 1; point <= digitsCount; point++) {
                int length = digitsCount
                        + (point < digitsCount ? 1 : 0)
                        + 1
                        + Integer.toString(exponent + digitsCount - point).length();
                scientificLength = Math.min(scientificLength, length);
            }
            return signLength + Math.min(fixedLength, scientificLength);
        }

        private static List<BigInteger> firstFeasibleCoefficients(
                Fraction lower, Fraction upper, int exponent, int digitsCount) {
            BigInteger least = digitsCount == 1 ? BigInteger.ONE : pow10(digitsCount - 1);
            BigInteger greatest = pow10(digitsCount).subtract(BigInteger.ONE);
            BigInteger first = scaledFloor(lower, exponent).subtract(BigInteger.ONE).max(least);
            BigInteger last = scaledFloor(upper, exponent).add(BigInteger.ONE).min(greatest);
            if (first.compareTo(last) > 0) {
                return List.of();
            }
            BigInteger capped = first.add(BigInteger.valueOf(3)).min(last);
            List<BigInteger> result = new ArrayList<>(4);
            for (BigInteger value = first; value.compareTo(capped) <= 0; value = value.add(BigInteger.ONE)) {
                result.add(value);
            }
            return result;
        }

        private static int decimalOrder(Fraction value) {
            int order = value.numerator.toString().length() - value.denominator.toString().length();
            if (order >= 0) {
                if (value.numerator.compareTo(value.denominator.multiply(pow10(order))) < 0) {
                    return order - 1;
                }
            } else if (value.numerator.multiply(pow10(-order)).compareTo(value.denominator) < 0) {
                return order - 1;
            }
            return order;
        }

        private static BigInteger scaledFloor(Fraction value, int exponent) {
            return exponent >= 0
                    ? value.numerator.divide(value.denominator.multiply(pow10(exponent)))
                    : value.numerator.multiply(pow10(-exponent)).divide(value.denominator);
        }

        private static boolean parsesAs(String spelling, double expected) {
            try {
                return Double.doubleToRawLongBits(Double.parseDouble(spelling))
                        == Double.doubleToRawLongBits(expected);
            } catch (NumberFormatException error) {
                return false;
            }
        }

        private static boolean better(String candidate, String current) {
            return candidate.length() < current.length()
                    || (candidate.length() == current.length() && candidate.compareTo(current) < 0);
        }

        private static boolean betterOrEqual(String candidate, String current) {
            return candidate.equals(current) || better(candidate, current);
        }

        private static String stripLeadingZeros(String value) {
            int index = 0;
            while (index < value.length() && value.charAt(index) == '0') index++;
            return value.substring(index);
        }

        private static BigInteger pow10(int exponent) {
            return exponent < POW10.length ? POW10[exponent] : TEN.pow(exponent);
        }

        private static BigInteger[] powersOfTen() {
            BigInteger[] values = new BigInteger[400];
            values[0] = BigInteger.ONE;
            for (int index = 1; index < values.length; index++) {
                values[index] = values[index - 1].multiply(TEN);
            }
            return values;
        }

        private record Fraction(BigInteger numerator, BigInteger denominator) {
            private Fraction {
                if (denominator.signum() <= 0) throw new IllegalArgumentException("denominator");
                BigInteger divisor = numerator.gcd(denominator);
                numerator = numerator.divide(divisor);
                denominator = denominator.divide(divisor);
            }

            private static Fraction fromDouble(double value) {
                if (!Double.isFinite(value) || value < 0.0d) {
                    throw new IllegalArgumentException("fraction requires finite non-negative binary64");
                }
                if (value == 0.0d) {
                    return new Fraction(BigInteger.ZERO, BigInteger.ONE);
                }
                long bits = Double.doubleToRawLongBits(value);
                int exponentBits = (int) ((bits >>> 52) & 0x7ffL);
                long fractionBits = bits & 0x000f_ffff_ffff_ffffL;
                BigInteger significand;
                int exponent;
                if (exponentBits == 0) {
                    significand = BigInteger.valueOf(fractionBits);
                    exponent = -1074;
                } else {
                    significand = BigInteger.valueOf(fractionBits | (1L << 52));
                    exponent = exponentBits - 1023 - 52;
                }
                return exponent >= 0
                        ? new Fraction(significand.shiftLeft(exponent), BigInteger.ONE)
                        : new Fraction(significand, BigInteger.ONE.shiftLeft(-exponent));
            }

            private Fraction add(Fraction other) {
                return new Fraction(
                        numerator.multiply(other.denominator).add(other.numerator.multiply(denominator)),
                        denominator.multiply(other.denominator));
            }

            private Fraction subtract(Fraction other) {
                return new Fraction(
                        numerator.multiply(other.denominator).subtract(other.numerator.multiply(denominator)),
                        denominator.multiply(other.denominator));
            }

            private Fraction half() {
                return new Fraction(numerator, denominator.shiftLeft(1));
            }
        }
    }
}
