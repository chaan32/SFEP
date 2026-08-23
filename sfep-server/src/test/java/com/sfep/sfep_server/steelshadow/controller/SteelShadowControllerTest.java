package com.sfep.sfep_server.steelshadow.controller;

import com.sfep.sfep_server.steelshadow.client.SteelShadowClient;
import com.sfep.sfep_server.steelshadow.client.SteelShadowClientException;
import com.sfep.sfep_server.steelshadow.dto.SteelShadowFeatureBatchRequest;
import com.sfep.sfep_server.steelshadow.dto.SteelShadowResponses.Prediction;
import com.sfep.sfep_server.steelshadow.dto.SteelShadowResponses.SteelShadowErrorResponse;
import com.sfep.sfep_server.steelshadow.dto.SteelShadowResponses.SteelShadowScoreResponse;
import io.micrometer.core.instrument.MeterRegistry;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.autoconfigure.web.servlet.AutoConfigureMockMvc;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.http.HttpStatus;
import org.springframework.test.context.bean.override.mockito.MockitoBean;
import org.springframework.test.web.servlet.MockMvc;

import java.util.List;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;
import static org.springframework.http.MediaType.APPLICATION_JSON;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

@SpringBootTest(properties = "sfep.steel-shadow.enabled=true")
@AutoConfigureMockMvc
class SteelShadowControllerTest {

    @Autowired
    MockMvc mvc;

    @Autowired
    MeterRegistry meterRegistry;

    @MockitoBean
    SteelShadowClient client;

    @Test
    void featureEndpointValidatesForwardsAndNeverReportsDeploymentEligible() throws Exception {
        when(client.score(any())).thenReturn(new SteelShadowScoreResponse(
                "steel-shadow-score-response-v1",
                "hr-20260822-a-001",
                "batch-1",
                "INDEPENDENT_FUTURE_SHADOW",
                1,
                List.of(new Prediction(
                        "H1", "shadow_incumbent", "model-1", 0.18,
                        null, 0.85, "양품"
                )),
                "COLLECTING",
                false
        ));

        mvc.perform(post("/api/steel-shadow/features")
                        .contentType(APPLICATION_JSON)
                        .content(validFeatureJson()))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.requestId").value("hr-20260822-a-001"))
                .andExpect(jsonPath("$.predictions.length()").value(1))
                .andExpect(jsonPath("$.deploymentEligible").value(false));

        var request = org.mockito.ArgumentCaptor.forClass(
                SteelShadowFeatureBatchRequest.class
        );
        verify(client).score(request.capture());
        assertThat(request.getValue().coils().getFirst().features().smPlant())
                .isEqualTo("1제강");
        assertThat(meterRegistry
                .find("sfep_steel_shadow_requests_total")
                .tags("operation", "features", "outcome", "SUCCESS")
                .counter()
                .count()).isGreaterThanOrEqualTo(1.0);
        assertThat(meterRegistry
                .find("sfep_steel_shadow_request_duration_seconds")
                .tag("operation", "features")
                .timer()
                .count()).isGreaterThanOrEqualTo(1L);
    }

    @Test
    void invalidSchemaIsRejectedBeforeCallingSidecar() throws Exception {
        mvc.perform(post("/api/steel-shadow/features")
                        .contentType(APPLICATION_JSON)
                        .content(validFeatureJson().replace(
                                "steel-shadow-feature-batch-v1", "wrong-version"
                        )))
                .andExpect(status().isBadRequest())
                .andExpect(jsonPath("$.schemaVersion").value("steel-shadow-error-v1"))
                .andExpect(jsonPath("$.requestId").value("hr-20260822-a-001"))
                .andExpect(jsonPath("$.code").value("REQUEST_SCHEMA_INVALID"))
                .andExpect(jsonPath("$.retryable").value(false));

        verify(client, never()).score(any());
    }

    @Test
    void forbiddenEngineeredFeatureIsRejectedInsteadOfSilentlyDropped() throws Exception {
        String payload = validFeatureJson().replace(
                "\"slab_width\":1250.0",
                "\"slab_width\":1250.0,\"gas_total\":15.0"
        );

        mvc.perform(post("/api/steel-shadow/features")
                        .contentType(APPLICATION_JSON)
                        .content(payload))
                .andExpect(status().isBadRequest())
                .andExpect(jsonPath("$.requestId").value("hr-20260822-a-001"))
                .andExpect(jsonPath("$.code").value("REQUEST_SCHEMA_INVALID"));

        verify(client, never()).score(any());
    }

    @Test
    void oversizedRequestReturnsPayloadTooLargeBeforeCallingSidecar() throws Exception {
        byte[] payload = new byte[10 * 1024 * 1024 + 1];

        mvc.perform(post("/api/steel-shadow/features")
                        .contentType(APPLICATION_JSON)
                        .content(payload))
                .andExpect(status().isPayloadTooLarge())
                .andExpect(jsonPath("$.code").value("REQUEST_SCHEMA_INVALID"))
                .andExpect(jsonPath("$.retryable").value(false));

        verify(client, never()).score(any());
    }

    @Test
    void unreachableSidecarReturnsSafe503AndRecordsUnavailableMetric() throws Exception {
        when(client.status()).thenThrow(new SteelShadowClientException(
                HttpStatus.SERVICE_UNAVAILABLE,
                new SteelShadowErrorResponse(
                        "steel-shadow-error-v1",
                        null,
                        "SHADOW_UNAVAILABLE",
                        "Shadow sidecar is unavailable",
                        true
                ),
                null
        ));

        mvc.perform(get("/api/steel-shadow/status"))
                .andExpect(status().isServiceUnavailable())
                .andExpect(jsonPath("$.code").value("SHADOW_UNAVAILABLE"))
                .andExpect(jsonPath("$.retryable").value(true));

        assertThat(meterRegistry
                .find("sfep_steel_shadow_unavailable_total")
                .counter()
                .count()).isGreaterThanOrEqualTo(1.0);
        assertThat(meterRegistry
                .find("sfep_steel_shadow_requests_total")
                .tags("operation", "status", "outcome", "SHADOW_UNAVAILABLE")
                .counter()
                .count()).isGreaterThanOrEqualTo(1.0);
    }

    private static String validFeatureJson() {
        return """
                {
                  "schemaVersion":"steel-shadow-feature-batch-v1",
                  "requestId":"hr-20260822-a-001",
                  "coils":[{
                    "chargeId":"C1",
                    "slabNo":"1",
                    "hrCoilId":"H1",
                    "hrDate":"2026-08-22",
                    "featureAvailableAt":"2026-08-22T03:15:00+00:00",
                    "features":{
                      "sm_plant":"1제강",
                      "steel_grade":"GRADE-A",
                      "steel_usage":"USE-A",
                      "delta_ferrite":0.0,
                      "ingre_cr":0.0,
                      "ingre_ni":0.0,
                      "ingre_s":0.0,
                      "cc_gubun":"CC-A",
                      "tundish_temp":1500.0,
                      "mlac_ratio":50.0,
                      "slab_gubun":"SLAB-A",
                      "slab_grind":"HSHS",
                      "furnace_no":"1호기",
                      "f_jangip_gubun":"A",
                      "f_jangip_temp":700.0,
                      "f_bfg":10.0,
                      "f_cog":5.0,
                      "f_ldg":0.0,
                      "f_pre_temp":1100.0,
                      "f_heat_temp":1250.0,
                      "f_sock_temp":1250.0,
                      "f_pre_interval":10.0,
                      "f_heat_interval":20.0,
                      "f_sock_interval":10.0,
                      "f_ext_time":1.0,
                      "hr_thick":2.0,
                      "hr_width":1248.0,
                      "rm4_temp":950.0,
                      "rm_pitch":1.0,
                      "slab_width":1250.0
                    }
                  }]
                }
                """;
    }
}
