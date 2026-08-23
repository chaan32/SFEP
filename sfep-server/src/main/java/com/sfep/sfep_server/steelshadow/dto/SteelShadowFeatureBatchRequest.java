package com.sfep.sfep_server.steelshadow.dto;

import com.fasterxml.jackson.annotation.JsonProperty;
import jakarta.validation.Valid;
import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.NotEmpty;
import jakarta.validation.constraints.NotNull;
import jakarta.validation.constraints.Pattern;
import jakarta.validation.constraints.Size;

import java.time.LocalDate;
import java.time.OffsetDateTime;
import java.util.List;

public record SteelShadowFeatureBatchRequest(
        @NotBlank
        @Pattern(regexp = "steel-shadow-feature-batch-v1")
        String schemaVersion,
        @NotBlank
        @Size(max = 128)
        @Pattern(regexp = "[\\x20-\\x7E]+")
        String requestId,
        @NotEmpty
        @Size(max = 10_000)
        List<@Valid Coil> coils
) {
    public record Coil(
            @NotBlank String chargeId,
            @NotBlank String slabNo,
            @NotBlank String hrCoilId,
            @NotNull LocalDate hrDate,
            @NotNull OffsetDateTime featureAvailableAt,
            @NotNull @Valid ProcessFeatures features
    ) {
    }

    public record ProcessFeatures(
            @JsonProperty("sm_plant") @NotBlank String smPlant,
            @JsonProperty("steel_grade") @NotBlank String steelGrade,
            @JsonProperty("steel_usage") @NotBlank String steelUsage,
            @JsonProperty("delta_ferrite") @NotNull Double deltaFerrite,
            @JsonProperty("ingre_cr") @NotNull Double ingreCr,
            @JsonProperty("ingre_ni") @NotNull Double ingreNi,
            @JsonProperty("ingre_s") @NotNull Double ingreS,
            @JsonProperty("cc_gubun") @NotBlank String ccGubun,
            @JsonProperty("tundish_temp") @NotNull Double tundishTemp,
            @JsonProperty("mlac_ratio") @NotNull Double mlacRatio,
            @JsonProperty("slab_gubun") @NotBlank String slabGubun,
            @JsonProperty("slab_grind") @NotBlank String slabGrind,
            @JsonProperty("furnace_no") @NotBlank String furnaceNo,
            @JsonProperty("f_jangip_gubun") @NotBlank String fJangipGubun,
            @JsonProperty("f_jangip_temp") @NotNull Double fJangipTemp,
            @JsonProperty("f_bfg") @NotNull Double fBfg,
            @JsonProperty("f_cog") @NotNull Double fCog,
            @JsonProperty("f_ldg") @NotNull Double fLdg,
            @JsonProperty("f_pre_temp") @NotNull Double fPreTemp,
            @JsonProperty("f_heat_temp") @NotNull Double fHeatTemp,
            @JsonProperty("f_sock_temp") @NotNull Double fSockTemp,
            @JsonProperty("f_pre_interval") @NotNull Double fPreInterval,
            @JsonProperty("f_heat_interval") @NotNull Double fHeatInterval,
            @JsonProperty("f_sock_interval") @NotNull Double fSockInterval,
            @JsonProperty("f_ext_time") @NotNull Double fExtTime,
            @JsonProperty("hr_thick") @NotNull Double hrThick,
            @JsonProperty("hr_width") @NotNull Double hrWidth,
            @JsonProperty("rm4_temp") @NotNull Double rm4Temp,
            @JsonProperty("rm_pitch") @NotNull Double rmPitch,
            @JsonProperty("slab_width") @NotNull Double slabWidth
    ) {
    }
}
