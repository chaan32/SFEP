package com.sfep.sfep_server.steelshadow.client;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.json.JsonMapper;
import com.sfep.sfep_server.steelshadow.dto.SteelShadowFeatureBatchRequest;
import com.sfep.sfep_server.steelshadow.dto.SteelShadowFeatureBatchRequest.Coil;
import com.sfep.sfep_server.steelshadow.dto.SteelShadowFeatureBatchRequest.ProcessFeatures;
import com.sun.net.httpserver.HttpExchange;
import com.sun.net.httpserver.HttpServer;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.Test;
import org.springframework.http.HttpStatus;
import org.springframework.web.client.RestClient;

import java.io.IOException;
import java.net.InetSocketAddress;
import java.net.ServerSocket;
import java.nio.charset.StandardCharsets;
import java.time.LocalDate;
import java.time.OffsetDateTime;
import java.util.List;
import java.util.Set;
import java.util.concurrent.atomic.AtomicInteger;
import java.util.concurrent.atomic.AtomicReference;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

class SteelShadowClientTest {

    private final ObjectMapper objectMapper = JsonMapper.builder().findAndAddModules().build();
    private HttpServer server;

    @AfterEach
    void stopServer() {
        if (server != null) {
            server.stop(0);
        }
    }

    @Test
    void forwardsExactFeatureContractAndParsesResponse() throws Exception {
        AtomicReference<String> capturedPath = new AtomicReference<>();
        AtomicReference<JsonNode> capturedJson = new AtomicReference<>();
        AtomicReference<String> capturedContentLength = new AtomicReference<>();
        AtomicReference<String> capturedTransferEncoding = new AtomicReference<>();
        AtomicInteger requestCount = new AtomicInteger();
        server = startServer("/v1/features", exchange -> {
            requestCount.incrementAndGet();
            capturedPath.set(exchange.getRequestURI().getPath());
            capturedContentLength.set(exchange.getRequestHeaders().getFirst("Content-Length"));
            capturedTransferEncoding.set(exchange.getRequestHeaders().getFirst("Transfer-Encoding"));
            capturedJson.set(objectMapper.readTree(exchange.getRequestBody()));
            respond(exchange, 200, """
                    {
                      "schemaVersion":"steel-shadow-score-response-v1",
                      "requestId":"hr-20260822-a-001",
                      "batchId":"batch-1",
                      "evidenceStatus":"INDEPENDENT_FUTURE_SHADOW",
                      "coilCount":1,
                      "predictions":[{
                        "hrCoilId":"H1",
                        "modelRole":"shadow_incumbent",
                        "modelId":"steel-quality-challenger-v0.2:logistic_l2_c0_1",
                        "riskScore":0.18,
                        "calibratedProbability":null,
                        "policyThreshold":0.85,
                        "predictedLabel":"양품"
                      }],
                      "shadowStatus":"COLLECTING",
                      "deploymentEligible":false
                    }
                    """);
        });

        var response = client(baseUrl()).score(featureRequest());

        assertThat(capturedPath).hasValue("/v1/features");
        assertThat(capturedContentLength.get()).isNotBlank();
        assertThat(capturedTransferEncoding.get()).isNull();
        assertThat(capturedJson.get().at("/schemaVersion").asText())
                .isEqualTo("steel-shadow-feature-batch-v1");
        assertThat(capturedJson.get().at("/coils/0/features/sm_plant").asText())
                .isEqualTo("1제강");
        assertThat(capturedJson.get().propertyStream().map(java.util.Map.Entry::getKey))
                .containsExactlyInAnyOrder("schemaVersion", "requestId", "coils");
        assertThat(capturedJson.get().at("/coils/0").propertyStream()
                .map(java.util.Map.Entry::getKey))
                .containsExactlyInAnyOrder(
                        "chargeId", "slabNo", "hrCoilId", "hrDate",
                        "featureAvailableAt", "features"
                );
        assertThat(capturedJson.get().at("/coils/0/features").propertyStream()
                .map(java.util.Map.Entry::getKey))
                .containsExactlyInAnyOrderElementsOf(Set.of(
                        "sm_plant", "steel_grade", "steel_usage", "delta_ferrite",
                        "ingre_cr", "ingre_ni", "ingre_s", "cc_gubun", "tundish_temp",
                        "mlac_ratio", "slab_gubun", "slab_grind", "furnace_no",
                        "f_jangip_gubun", "f_jangip_temp", "f_bfg", "f_cog", "f_ldg",
                        "f_pre_temp", "f_heat_temp", "f_sock_temp", "f_pre_interval",
                        "f_heat_interval", "f_sock_interval", "f_ext_time", "hr_thick",
                        "hr_width", "rm4_temp", "rm_pitch", "slab_width"
                ));
        assertThat(requestCount).hasValue(1);
        assertThat(response.deploymentEligible()).isFalse();
        assertThat(response.predictions()).hasSize(1);
    }

    @Test
    void preservesSafeSidecarErrorWithoutRetryingMutation() throws Exception {
        AtomicInteger requestCount = new AtomicInteger();
        server = startServer("/v1/features", exchange -> {
            requestCount.incrementAndGet();
            respond(exchange, 409, """
                    {
                      "schemaVersion":"steel-shadow-error-v1",
                      "requestId":"hr-20260822-a-001",
                      "code":"COIL_ALREADY_SCORED_DIFFERENTLY",
                      "message":"one or more Coils were already scored differently",
                      "retryable":false
                    }
                    """);
        });

        assertThatThrownBy(() -> client(baseUrl()).score(featureRequest()))
                .isInstanceOfSatisfying(SteelShadowClientException.class, exception -> {
                    assertThat(exception.status()).isEqualTo(HttpStatus.CONFLICT);
                    assertThat(exception.error().code())
                            .isEqualTo("COIL_ALREADY_SCORED_DIFFERENTLY");
                    assertThat(exception.error().requestId()).isEqualTo("hr-20260822-a-001");
                    assertThat(exception.error().retryable()).isFalse();
                });
        assertThat(requestCount).hasValue(1);
    }

    @Test
    void rejectsAnyResponseThatClaimsDeploymentEligibility() throws Exception {
        server = startServer("/v1/features", exchange -> respond(exchange, 200, """
                {
                  "schemaVersion":"steel-shadow-score-response-v1",
                  "requestId":"hr-20260822-a-001",
                  "batchId":"batch-1",
                  "evidenceStatus":"INDEPENDENT_FUTURE_SHADOW",
                  "coilCount":1,
                  "predictions":[],
                  "shadowStatus":"COLLECTING",
                  "deploymentEligible":true
                }
                """));

        assertThatThrownBy(() -> client(baseUrl()).score(featureRequest()))
                .isInstanceOfSatisfying(SteelShadowClientException.class, exception -> {
                    assertThat(exception.status()).isEqualTo(HttpStatus.INTERNAL_SERVER_ERROR);
                    assertThat(exception.error().code()).isEqualTo("SHADOW_INTERNAL_ERROR");
                    assertThat(exception.error().retryable()).isFalse();
                });
    }

    @Test
    void rejectsSuccessResponseWithMismatchedRequestId() throws Exception {
        server = startServer("/v1/features", exchange -> respond(exchange, 200, """
                {
                  "schemaVersion":"steel-shadow-score-response-v1",
                  "requestId":"different-request",
                  "batchId":"batch-1",
                  "evidenceStatus":"INDEPENDENT_FUTURE_SHADOW",
                  "coilCount":1,
                  "predictions":[],
                  "shadowStatus":"COLLECTING",
                  "deploymentEligible":false
                }
                """));

        assertThatThrownBy(() -> client(baseUrl()).score(featureRequest()))
                .isInstanceOfSatisfying(SteelShadowClientException.class, exception -> {
                    assertThat(exception.status()).isEqualTo(HttpStatus.INTERNAL_SERVER_ERROR);
                    assertThat(exception.error().code()).isEqualTo("SHADOW_INTERNAL_ERROR");
                });
    }

    @Test
    void replacesMalformedSidecarErrorWithSafeInternalError() throws Exception {
        server = startServer("/v1/features", exchange -> respond(exchange, 409, "{}"));

        assertThatThrownBy(() -> client(baseUrl()).score(featureRequest()))
                .isInstanceOfSatisfying(SteelShadowClientException.class, exception -> {
                    assertThat(exception.status()).isEqualTo(HttpStatus.INTERNAL_SERVER_ERROR);
                    assertThat(exception.error().code()).isEqualTo("SHADOW_INTERNAL_ERROR");
                    assertThat(exception.error().requestId()).isEqualTo("hr-20260822-a-001");
                    assertThat(exception.error().retryable()).isFalse();
                });
    }

    @Test
    void replacesMalformedSuccessResponseWithSafeInternalError() throws Exception {
        server = startServer("/v1/features", exchange -> respond(exchange, 200, "{"));

        assertThatThrownBy(() -> client(baseUrl()).score(featureRequest()))
                .isInstanceOfSatisfying(SteelShadowClientException.class, exception -> {
                    assertThat(exception.status()).isEqualTo(HttpStatus.INTERNAL_SERVER_ERROR);
                    assertThat(exception.error().code()).isEqualTo("SHADOW_INTERNAL_ERROR");
                    assertThat(exception.error().requestId()).isEqualTo("hr-20260822-a-001");
                    assertThat(exception.error().retryable()).isFalse();
                });
    }

    @Test
    void mapsConnectionFailureToRetryableUnavailable() throws Exception {
        int unavailablePort;
        try (ServerSocket socket = new ServerSocket(0)) {
            unavailablePort = socket.getLocalPort();
        }

        assertThatThrownBy(() -> client("http://127.0.0.1:" + unavailablePort).status())
                .isInstanceOfSatisfying(SteelShadowClientException.class, exception -> {
                    assertThat(exception.status()).isEqualTo(HttpStatus.SERVICE_UNAVAILABLE);
                    assertThat(exception.error().code()).isEqualTo("SHADOW_UNAVAILABLE");
                    assertThat(exception.error().retryable()).isTrue();
                });
    }

    private SteelShadowClient client(String baseUrl) {
        return new SteelShadowClient(
                RestClient.builder().baseUrl(baseUrl).build(),
                objectMapper
        );
    }

    private HttpServer startServer(String path, ThrowingHandler handler) throws IOException {
        HttpServer httpServer = HttpServer.create(new InetSocketAddress("127.0.0.1", 0), 0);
        httpServer.createContext(path, exchange -> {
            try {
                handler.handle(exchange);
            } finally {
                exchange.close();
            }
        });
        httpServer.start();
        return httpServer;
    }

    private String baseUrl() {
        return "http://127.0.0.1:" + server.getAddress().getPort();
    }

    private static void respond(HttpExchange exchange, int status, String json) throws IOException {
        byte[] body = json.getBytes(StandardCharsets.UTF_8);
        exchange.getResponseHeaders().set("Content-Type", "application/json; charset=utf-8");
        exchange.sendResponseHeaders(status, body.length);
        exchange.getResponseBody().write(body);
    }

    private static SteelShadowFeatureBatchRequest featureRequest() {
        return new SteelShadowFeatureBatchRequest(
                "steel-shadow-feature-batch-v1",
                "hr-20260822-a-001",
                List.of(new Coil(
                        "C1",
                        "1",
                        "H1",
                        LocalDate.parse("2026-08-22"),
                        OffsetDateTime.parse("2026-08-22T03:15:00+00:00"),
                        new ProcessFeatures(
                                "1제강", "GRADE-A", "USE-A", 0.0, 0.0, 0.0, 0.0,
                                "CC-A", 1500.0, 50.0, "SLAB-A", "HSHS", "1호기", "A",
                                700.0, 10.0, 5.0, 0.0, 1100.0, 1250.0, 1250.0,
                                10.0, 20.0, 10.0, 1.0, 2.0, 1248.0, 950.0, 1.0, 1250.0
                        )
                ))
        );
    }

    @FunctionalInterface
    private interface ThrowingHandler {
        void handle(HttpExchange exchange) throws IOException;
    }
}
