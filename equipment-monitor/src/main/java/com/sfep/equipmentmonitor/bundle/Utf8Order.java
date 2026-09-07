package com.sfep.equipmentmonitor.bundle;

import java.nio.charset.StandardCharsets;
import java.util.Comparator;

public final class Utf8Order {
    public static final Comparator<String> COMPARATOR = Utf8Order::compare;

    private Utf8Order() {
    }

    public static int compare(String left, String right) {
        byte[] a = left.getBytes(StandardCharsets.UTF_8);
        byte[] b = right.getBytes(StandardCharsets.UTF_8);
        int common = Math.min(a.length, b.length);
        for (int i = 0; i < common; i++) {
            int difference = Byte.toUnsignedInt(a[i]) - Byte.toUnsignedInt(b[i]);
            if (difference != 0) {
                return difference;
            }
        }
        return Integer.compare(a.length, b.length);
    }
}
