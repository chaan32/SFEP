package com.sfep.equipmentmonitor.bundle;

import com.networknt.schema.Schema;

public final class SchemaDocument {
    private final byte[] bytes;
    private final String sha256;
    private final Schema schema;

    SchemaDocument(byte[] bytes, String sha256, Schema schema) {
        this.bytes = bytes.clone();
        this.sha256 = sha256;
        this.schema = schema;
    }

    public byte[] bytes() {
        return bytes.clone();
    }

    public String sha256() {
        return sha256;
    }

    Schema schema() {
        return schema;
    }
}
