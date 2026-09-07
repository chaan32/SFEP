package com.sfep.equipmentmonitor.risk;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.node.JsonNodeFactory;
import com.fasterxml.jackson.databind.node.ObjectNode;
import com.sfep.equipmentmonitor.bundle.DigestIds;

import java.util.List;

/** Reconstructs the label-free identity preimages fixed by the Python producer contract. */
public final class RiskDefinitionIds {
    private static final List<String> QUALITY_ID_FIELDS = List.of(
            "analysisFamily", "fieldNames", "predicate", "firstAvailableStage", "equipmentType",
            "applicationScope", "equipmentId", "applicationContext", "adjustmentLevel",
            "adjustmentFieldsDropped", "adjustmentKind");

    private RiskDefinitionIds() {
    }

    public static String operatingRangeId(JsonNode definition) {
        ObjectNode preimage = JsonNodeFactory.instance.objectNode();
        definition.fields().forEachRemaining(entry -> {
            if (!entry.getKey().equals("ruleId")) {
                preimage.set(entry.getKey(), entry.getValue());
            }
        });
        return DigestIds.canonicalSha256Uri(preimage);
    }

    public static String qualityRuleId(JsonNode definition) {
        ObjectNode preimage = JsonNodeFactory.instance.objectNode();
        for (String field : QUALITY_ID_FIELDS) {
            JsonNode value = definition.get(field);
            if (value == null) {
                throw new RiskDefinitionException("quality rule identity field missing: " + field);
            }
            preimage.set(field, value);
        }
        return DigestIds.canonicalSha256Uri(preimage);
    }
}
