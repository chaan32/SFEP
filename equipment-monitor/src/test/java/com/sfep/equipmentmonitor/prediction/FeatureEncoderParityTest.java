package com.sfep.equipmentmonitor.prediction;

import com.fasterxml.jackson.databind.JsonNode;
import com.sfep.equipmentmonitor.risk.RiskScalar;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;
import org.opentest4j.TestAbortedException;

import java.nio.file.Files;
import java.nio.file.Path;
import java.util.HashMap;
import java.util.HashSet;

import static org.junit.jupiter.api.Assertions.*;

class FeatureEncoderParityTest {
    @Test
    void matchesEveryPythonFloat32Bit() throws Exception {
        ModelDevArtifacts artifacts = ModelDevArtifacts.load();
        FeatureEncoder encoder = QualityModelMetadata.read(artifacts.metadata()).encoder();
        JsonNode raw = artifacts.vectors().path("raw");
        JsonNode matrix = artifacts.vectors().path("matrix");
        assertTrue(raw.isArray() && !raw.isEmpty() && matrix.isArray());
        assertEquals(raw.size(), matrix.size());
        for (int rowIndex = 0; rowIndex < raw.size(); rowIndex++) {
            ModelDevArtifacts.Row row = ModelDevArtifacts.Row.read(raw.get(rowIndex));
            FeatureEncoder.Encoded encoded = encoder.encode(row.values(), row.seenFields());
            assertFalse(encoded.inputIncomplete());
            float[] actual = encoded.vector();
            JsonNode expected = matrix.get(rowIndex);
            assertTrue(expected.isArray());
            assertEquals(expected.size(), actual.length);
            for (int column = 0; column < actual.length; column++) {
                assertTrue(expected.get(column).isNumber());
                assertEquals(Float.floatToRawIntBits((float) expected.get(column).doubleValue()),
                        Float.floatToRawIntBits(actual[column]), "row " + rowIndex + " column " + column);
            }
        }
    }

    @Test
    void distinguishesExplicitNullUnknownAndUnseenFields() throws Exception {
        ModelDevArtifacts artifacts = ModelDevArtifacts.load();
        FeatureEncoder encoder = QualityModelMetadata.read(artifacts.metadata()).encoder();
        JsonNode features = ModelDevArtifacts.read(artifacts.metadata()).path("features").path("features");
        int unknown = features.get(0).path("vocabulary").size() - 1;
        ModelDevArtifacts.Row row = ModelDevArtifacts.Row.read(artifacts.vectors().path("raw").get(0));
        var values = new HashMap<>(row.values());
        values.put("sm_plant", RiskScalar.of("unregistered"));
        var encoded = encoder.encode(values, row.seenFields());
        assertEquals(1.0f, encoded.vector()[unknown]);
        assertEquals(0.0f, encoded.vector()[unknown + 1]);
        values.remove("sm_plant");
        encoded = encoder.encode(values, row.seenFields());
        assertEquals(1.0f, encoded.vector()[unknown]);
        assertEquals(1.0f, encoded.vector()[unknown + 1]);
        int firstNumeric = 0;
        for (int index = 0; index < 5; index++) {
            firstNumeric += features.get(index).path("vocabulary").size() + 1;
        }
        values.remove("tundish_temp");
        encoded = encoder.encode(values, row.seenFields());
        assertEquals((float) features.get(5).path("median").doubleValue(), encoded.vector()[firstNumeric]);
        assertEquals(1.0f, encoded.vector()[firstNumeric + 1]);
        var unseen = new HashSet<>(row.seenFields());
        unseen.remove("sm_plant");
        assertThrows(IllegalArgumentException.class, () -> encoder.encode(values, unseen));
        values.put("sm_plant", RiskScalar.of(2));
        assertThrows(IllegalArgumentException.class, () -> encoder.encode(values, row.seenFields()));
    }

    @Test
    void skipsAbsentArtifactsAndRejectsPartialArtifacts(@TempDir Path directory) throws Exception {
        assertThrows(TestAbortedException.class, () -> ModelDevArtifacts.load(directory));
        Files.writeString(directory.resolve("quality_model.json"), "{}");
        assertThrows(AssertionError.class, () -> ModelDevArtifacts.load(directory));
    }
}
