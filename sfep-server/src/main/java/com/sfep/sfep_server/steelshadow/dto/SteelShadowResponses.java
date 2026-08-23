package com.sfep.sfep_server.steelshadow.dto;

import com.fasterxml.jackson.annotation.JsonIgnoreProperties;

import java.util.List;

public final class SteelShadowResponses {
    private SteelShadowResponses() {
    }

    public interface ShadowOnlyResponse {
        String schemaVersion();

        boolean deploymentEligible();
    }

    @JsonIgnoreProperties(ignoreUnknown = true)
    public record Prediction(
            String hrCoilId,
            String modelRole,
            String modelId,
            double riskScore,
            Double calibratedProbability,
            double policyThreshold,
            String predictedLabel
    ) {
    }

    @JsonIgnoreProperties(ignoreUnknown = true)
    public record SteelShadowScoreResponse(
            String schemaVersion,
            String requestId,
            String batchId,
            String evidenceStatus,
            int coilCount,
            List<Prediction> predictions,
            String shadowStatus,
            boolean deploymentEligible
    ) implements ShadowOnlyResponse {
    }

    @JsonIgnoreProperties(ignoreUnknown = true)
    public record SteelShadowLabelResponse(
            String schemaVersion,
            String requestId,
            String batchId,
            int labelCount,
            int positiveLabels,
            String shadowStatus,
            boolean deploymentEligible
    ) implements ShadowOnlyResponse {
    }

    @JsonIgnoreProperties(ignoreUnknown = true)
    public record SteelShadowStatusResponse(
            String schemaVersion,
            String status,
            String evidenceStatus,
            int predictionBatches,
            int labelBatches,
            int days,
            int labeledCoils,
            int positiveLabels,
            List<String> failedChecks,
            boolean deploymentEligible
    ) implements ShadowOnlyResponse {
    }

    @JsonIgnoreProperties(ignoreUnknown = true)
    public record SteelShadowPredictionResponse(
            String schemaVersion,
            String hrCoilId,
            String batchId,
            List<Prediction> predictions,
            boolean deploymentEligible
    ) implements ShadowOnlyResponse {
    }

    @JsonIgnoreProperties(ignoreUnknown = true)
    public record HealthModel(String modelId, String modelRole) {
    }

    @JsonIgnoreProperties(ignoreUnknown = true)
    public record SteelShadowHealthResponse(
            String schemaVersion,
            String status,
            String serviceVersion,
            String registrySha256,
            List<HealthModel> models,
            boolean deploymentEligible
    ) implements ShadowOnlyResponse {
    }

    @JsonIgnoreProperties(ignoreUnknown = true)
    public record SteelShadowErrorResponse(
            String schemaVersion,
            String requestId,
            String code,
            String message,
            boolean retryable
    ) {
    }
}
