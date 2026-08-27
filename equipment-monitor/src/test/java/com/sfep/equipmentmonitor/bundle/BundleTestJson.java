package com.sfep.equipmentmonitor.bundle;

import com.fasterxml.jackson.databind.ObjectMapper;

final class BundleTestJson {
    private static final ObjectMapper MAPPER = new ObjectMapper();

    private BundleTestJson() {
    }

    static ObjectMapper mapper() {
        return MAPPER;
    }
}
