package com.sfep.equipmentmonitor.prediction;

public record QualityPrediction(PredictionStatus status, Double probability, String modelId, String qualification,
                                Double threshold) {
    public boolean shouldAlert() {
        return status == PredictionStatus.AVAILABLE && "QUALIFIED".equals(qualification)
                && probability != null && QualityModelMetadata.validProbability(probability)
                && threshold != null && QualityModelMetadata.validProbability(threshold) && probability >= threshold;
    }
}
