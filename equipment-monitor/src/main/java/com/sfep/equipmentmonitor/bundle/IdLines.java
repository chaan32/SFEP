package com.sfep.equipmentmonitor.bundle;

import java.nio.charset.StandardCharsets;
import java.util.Map;

public final class IdLines {
    private IdLines() {
    }

    public static IdValue compute(String namespace, Map<String, String> fields) {
        rejectSeparator(namespace, "namespace");
        StringBuilder preimage = new StringBuilder(namespace).append('\n');
        fields.entrySet().stream()
                .sorted(Map.Entry.comparingByKey(Utf8Order.COMPARATOR))
                .forEach(entry -> {
                    rejectSeparator(entry.getKey(), "key");
                    rejectSeparator(entry.getValue(), "value");
                    preimage.append(entry.getKey()).append('=').append(entry.getValue()).append('\n');
                });
        String text = preimage.toString();
        return new IdValue(Digests.sha256Uri(text.getBytes(StandardCharsets.UTF_8)), text);
    }

    private static void rejectSeparator(String value, String label) {
        if (value == null || value.indexOf('=') >= 0 || value.indexOf('\r') >= 0 || value.indexOf('\n') >= 0) {
            throw new IllegalArgumentException(label + " contains an id-lines separator");
        }
    }
}
