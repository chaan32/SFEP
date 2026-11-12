package com.sfep.equipmentmonitor.bundle;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.node.ObjectNode;
import com.sfep.equipmentmonitor.prediction.OnnxQualityPredictor;
import com.sfep.equipmentmonitor.prediction.QualityModelMetadata;

import java.nio.ByteBuffer;
import java.nio.charset.CharacterCodingException;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.List;
import java.util.Map;

/** Verified model bytes retained from the bundle read, with no open native resources. */
public final class QualityModelArtifact {
    private static final String MODEL_ID_NAMESPACE = "sfep-quality-model-id/v1";
    private static final String ML_DOMAIN = "ai.onnx.ml";
    private static final int FLOAT = 1;
    private static final int INT64 = 7;
    private static final long DYNAMIC = -1;

    private final byte[] onnx;
    private final QualityModelMetadata metadata;

    private QualityModelArtifact(byte[] onnx, QualityModelMetadata metadata) {
        this.onnx = onnx;
        this.metadata = metadata;
    }

    static QualityModelArtifact verify(byte[] onnx, JsonNode node, String claimedId) {
        ObjectNode preimage = node.deepCopy();
        String id = preimage.remove("modelId").textValue();
        String expected = IdLines.compute(MODEL_ID_NAMESPACE,
                Map.of("metadata_sha256", DigestIds.canonicalSha256Uri(preimage))).id();
        if (!expected.equals(id) || !expected.equals(claimedId)) {
            throw new BundleLoadException("MODEL_ID_MISMATCH", expected);
        }

        QualityModelMetadata metadata = QualityModelMetadata.parse(node, id);
        if (!metadata.onnxSha256().equals(Digests.sha256Uri(onnx))) {
            throw new BundleLoadException("ONNX_DIGEST_MISMATCH", id);
        }
        JsonNode contract = node.path("onnx");
        JsonNode width = contract.path("input").path("shape").path(1);
        if (!width.canConvertToInt() || width.intValue() != metadata.inputWidth()) {
            throw invalid("input width differs from the feature encoder");
        }
        Map<String, Long> opsets = new HashMap<>();
        contract.path("opsets").properties().forEach(entry -> {
            if (!entry.getValue().canConvertToLong()) {
                throw invalid("opset version is out of range");
            }
            opsets.put(entry.getKey(), entry.getValue().longValue());
        });
        if (!Long.valueOf(21).equals(opsets.get(""))
                || (opsets.containsKey(ML_DOMAIN) && !Long.valueOf(1).equals(opsets.get(ML_DOMAIN)))) {
            throw invalid("unsupported opset");
        }
        verifyGraph(onnx, opsets, metadata.inputWidth());
        return new QualityModelArtifact(onnx, metadata);
    }

    public QualityModelMetadata metadata() {
        return metadata;
    }

    public OnnxQualityPredictor openPredictor() {
        return OnnxQualityPredictor.open(onnx, metadata);
    }

    private static void verifyGraph(byte[] onnx, Map<String, Long> expectedOpsets, int width) {
        Proto model = new Proto(ByteBuffer.wrap(onnx));
        Proto graph = null;
        Map<String, Long> opsets = new HashMap<>();
        while (model.next()) {
            switch (model.field) {
                case 7 -> {
                    if (graph != null) throw invalid("duplicate graph");
                    graph = model.message();
                }
                case 8 -> readOpset(model.message(), opsets);
                case 20, 25, 26 -> throw invalid("training graphs, functions or device configuration are unsupported");
                default -> model.skip();
            }
        }
        if (graph == null || !expectedOpsets.equals(opsets)) {
            throw invalid("graph or opset does not match metadata");
        }
        Map<String, Tensor> inputs = new HashMap<>();
        Map<String, Tensor> outputs = new HashMap<>();
        while (graph.next()) {
            switch (graph.field) {
                case 1 -> verifyNode(graph.message(), opsets);
                case 5 -> verifyInlineTensor(graph.message());
                case 11 -> readValue(graph.message(), inputs);
                case 12 -> readValue(graph.message(), outputs);
                case 15 -> throw invalid("sparse initializers are unsupported");
                default -> graph.skip();
            }
        }
        if (!inputs.equals(Map.of("features", new Tensor(FLOAT, List.of(DYNAMIC, (long) width))))
                || !outputs.equals(Map.of("label", new Tensor(INT64, List.of(DYNAMIC)),
                        "probabilities", new Tensor(FLOAT, List.of(DYNAMIC, 2L))))) {
            throw invalid("unexpected input or output tensor contract");
        }
    }

    private static void readOpset(Proto entry, Map<String, Long> opsets) {
        String domain = "";
        long version = 0;
        while (entry.next()) {
            switch (entry.field) {
                case 1 -> domain = entry.string();
                case 2 -> version = entry.integer();
                default -> entry.skip();
            }
        }
        Long previous = opsets.putIfAbsent(domain, version);
        // The exporter can emit identical default-domain imports.
        if (version <= 0 || (previous != null && previous != version)) {
            throw invalid("invalid or conflicting opset");
        }
    }

    private static void verifyNode(Proto node, Map<String, Long> opsets) {
        String domain = "";
        while (node.next()) {
            switch (node.field) {
                case 5 -> verifyAttribute(node.message());
                case 7 -> domain = node.string();
                case 10 -> throw invalid("device configuration is unsupported");
                default -> node.skip();
            }
        }
        if (!opsets.containsKey(domain)) {
            throw invalid("node domain is not declared by the verified opsets");
        }
    }

    private static void verifyAttribute(Proto attribute) {
        while (attribute.next()) {
            switch (attribute.field) {
                case 5, 10 -> verifyInlineTensor(attribute.message());
                case 6, 11, 22, 23 -> throw invalid("subgraph and sparse tensor attributes are unsupported");
                default -> attribute.skip();
            }
        }
    }

    private static void verifyInlineTensor(Proto tensor) {
        while (tensor.next()) {
            if (tensor.field == 13 || (tensor.field == 14 && tensor.integer() != 0)) {
                throw invalid("external tensor data is unsupported");
            }
            if (tensor.field != 14) {
                tensor.skip();
            }
        }
    }

    private static void readValue(Proto value, Map<String, Tensor> tensors) {
        String name = null;
        Tensor tensor = null;
        while (value.next()) {
            switch (value.field) {
                case 1 -> name = value.string();
                case 2 -> {
                    if (tensor != null) throw invalid("duplicate value type");
                    tensor = readTensorType(value.message());
                }
                default -> value.skip();
            }
        }
        if (name == null || tensor == null || tensors.putIfAbsent(name, tensor) != null) {
            throw invalid("missing or duplicate graph value");
        }
    }

    private static Tensor readTensorType(Proto type) {
        Tensor tensor = null;
        while (type.next()) {
            if (type.field == 6) {
                type.skip();
            } else if (type.field == 1 && tensor == null) {
                tensor = readTensor(type.message());
            } else {
                throw invalid("expected one tensor type");
            }
        }
        if (tensor == null) throw invalid("missing tensor type");
        return tensor;
    }

    private static Tensor readTensor(Proto type) {
        long elementType = 0;
        List<Long> shape = null;
        while (type.next()) {
            switch (type.field) {
                case 1 -> elementType = type.integer();
                case 2 -> {
                    if (shape != null) throw invalid("duplicate tensor shape");
                    shape = readShape(type.message());
                }
                default -> type.skip();
            }
        }
        return new Tensor(elementType, shape);
    }

    private static List<Long> readShape(Proto shape) {
        List<Long> dimensions = new ArrayList<>(2);
        while (shape.next()) {
            if (shape.field != 1) {
                shape.skip();
                continue;
            }
            if (dimensions.size() == 2) throw invalid("unexpected tensor rank");
            Proto dimension = shape.message();
            long size = DYNAMIC;
            boolean specified = false;
            while (dimension.next()) {
                if (dimension.field == 1 || dimension.field == 2) {
                    if (specified) throw invalid("multiple values for one tensor dimension");
                    specified = true;
                    if (dimension.field == 1) {
                        size = dimension.integer();
                        if (size <= 0) throw invalid("nonpositive tensor dimension");
                    } else {
                        dimension.string();
                    }
                } else {
                    dimension.skip();
                }
            }
            dimensions.add(size);
        }
        return List.copyOf(dimensions);
    }

    private static BundleLoadException invalid(String detail) {
        return new BundleLoadException("INVALID_ONNX_CONTRACT", detail);
    }

    private record Tensor(long type, List<Long> shape) {
    }

    // Only the ONNX protobuf fields needed for pre-session validation are decoded.
    private static final class Proto {
        private final ByteBuffer bytes;
        private int field;
        private int wire;

        Proto(ByteBuffer bytes) {
            this.bytes = bytes;
        }

        boolean next() {
            if (!bytes.hasRemaining()) return false;
            long tag = varint();
            if (tag < 8 || tag > 0xffff_ffffL) throw invalid("invalid protobuf tag");
            field = (int) (tag >>> 3);
            wire = (int) (tag & 7);
            return true;
        }

        Proto message() {
            return new Proto(payload());
        }

        String string() {
            try {
                return StandardCharsets.UTF_8.newDecoder().decode(payload()).toString();
            } catch (CharacterCodingException error) {
                throw invalid("invalid protobuf UTF-8");
            }
        }

        long integer() {
            if (wire != 0) throw invalid("expected protobuf varint");
            return varint();
        }

        void skip() {
            switch (wire) {
                case 0 -> varint();
                case 1 -> advance(8);
                case 2 -> advance(varint());
                case 5 -> advance(4);
                default -> throw invalid("unsupported protobuf wire type");
            }
        }

        private ByteBuffer payload() {
            if (wire != 2) throw invalid("expected length-delimited protobuf field");
            long size = varint();
            int start = bytes.position();
            advance(size);
            return bytes.slice(start, (int) size);
        }

        private void advance(long size) {
            if (size < 0 || size > bytes.remaining()) throw invalid("truncated protobuf field");
            bytes.position(bytes.position() + (int) size);
        }

        private long varint() {
            long result = 0;
            for (int shift = 0; shift < 64 && bytes.hasRemaining(); shift += 7) {
                int part = Byte.toUnsignedInt(bytes.get());
                if (shift == 63 && part > 1) break;
                result |= (long) (part & 127) << shift;
                if (part < 128) return result;
            }
            throw invalid("invalid protobuf varint");
        }
    }
}
