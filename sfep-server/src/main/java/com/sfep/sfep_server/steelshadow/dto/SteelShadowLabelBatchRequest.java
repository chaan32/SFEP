package com.sfep.sfep_server.steelshadow.dto;

import jakarta.validation.Valid;
import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.NotEmpty;
import jakarta.validation.constraints.NotNull;
import jakarta.validation.constraints.Pattern;
import jakarta.validation.constraints.Size;

import java.time.OffsetDateTime;
import java.util.List;

public record SteelShadowLabelBatchRequest(
        @NotBlank
        @Pattern(regexp = "steel-shadow-label-batch-v1")
        String schemaVersion,
        @NotBlank
        @Size(max = 128)
        @Pattern(regexp = "[\\x20-\\x7E]+")
        String requestId,
        String supersedesLabelBatchId,
        @NotEmpty
        @Size(max = 10_000)
        List<@Valid Label> labels
) {
    public record Label(
            @NotBlank String hrCoilId,
            @NotBlank @Pattern(regexp = "양품|불량") String judge,
            @NotNull OffsetDateTime labelFinalizedAt
    ) {
    }
}
