package com.sfep.sfep_server.steelshadow;

import com.sfep.sfep_server.steelshadow.dto.SteelShadowFeatureBatchRequest;
import com.sfep.sfep_server.steelshadow.dto.SteelShadowFeatureBatchRequest.Coil;
import com.sfep.sfep_server.steelshadow.dto.SteelShadowFeatureBatchRequest.ProcessFeatures;
import com.sfep.sfep_server.steelshadow.dto.SteelShadowLabelBatchRequest;
import com.sfep.sfep_server.steelshadow.dto.SteelShadowLabelBatchRequest.Label;
import com.sfep.sfep_server.steelshadow.dto.SteelShadowResponses.SteelShadowLabelResponse;
import com.sfep.sfep_server.steelshadow.dto.SteelShadowResponses.SteelShadowPredictionResponse;
import com.sfep.sfep_server.steelshadow.dto.SteelShadowResponses.SteelShadowScoreResponse;
import com.sfep.sfep_server.steelshadow.dto.SteelShadowResponses.SteelShadowStatusResponse;
import org.junit.jupiter.api.AfterAll;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.boot.test.web.client.TestRestTemplate;
import org.springframework.test.context.DynamicPropertyRegistry;
import org.springframework.test.context.DynamicPropertySource;

import java.io.IOException;
import java.net.ServerSocket;
import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.nio.file.Files;
import java.nio.file.Path;
import java.time.Duration;
import java.time.OffsetDateTime;
import java.time.ZoneOffset;
import java.util.Comparator;
import java.util.List;

import static org.assertj.core.api.Assertions.assertThat;

@SpringBootTest(webEnvironment = SpringBootTest.WebEnvironment.RANDOM_PORT)
class SteelShadowEndToEndTest {

    private static Process sidecar;
    private static Path temporaryRoot;
    private static Path outputDir;
    private static int sidecarPort;

    @Autowired
    TestRestTemplate restTemplate;

    @DynamicPropertySource
    static synchronized void steelShadowProperties(DynamicPropertyRegistry registry) {
        if (sidecar == null) {
            startSidecar();
        }
        registry.add("sfep.steel-shadow.enabled", () -> "true");
        registry.add(
                "sfep.steel-shadow.base-url",
                () -> "http://127.0.0.1:" + sidecarPort
        );
    }

    @AfterAll
    static void stopSidecar() throws IOException {
        if (sidecar != null) {
            sidecar.destroy();
            try {
                if (!sidecar.waitFor(10, java.util.concurrent.TimeUnit.SECONDS)) {
                    sidecar.destroyForcibly();
                    sidecar.waitFor(10, java.util.concurrent.TimeUnit.SECONDS);
                }
            } catch (InterruptedException exception) {
                Thread.currentThread().interrupt();
                sidecar.destroyForcibly();
            }
        }
        if (temporaryRoot != null && Files.exists(temporaryRoot)) {
            try (var paths = Files.walk(temporaryRoot)) {
                for (Path path : paths.sorted(Comparator.reverseOrder()).toList()) {
                    Files.deleteIfExists(path);
                }
            }
        }
    }

    @Test
    void springAndPythonKeepRetriesIdempotentInTemporaryEvidence() throws Exception {
        SteelShadowFeatureBatchRequest features = featureRequest();

        SteelShadowScoreResponse firstScore = restTemplate.postForObject(
                "/api/steel-shadow/features", features, SteelShadowScoreResponse.class
        );
        SteelShadowScoreResponse secondScore = restTemplate.postForObject(
                "/api/steel-shadow/features", features, SteelShadowScoreResponse.class
        );

        assertThat(firstScore).isNotNull();
        assertThat(secondScore.batchId()).isEqualTo(firstScore.batchId());
        assertThat(firstScore.predictions()).hasSize(3);
        assertThat(firstScore.deploymentEligible()).isFalse();

        SteelShadowPredictionResponse prediction = restTemplate.getForObject(
                "/api/steel-shadow/predictions/E2E-H1",
                SteelShadowPredictionResponse.class
        );
        assertThat(prediction).isNotNull();
        assertThat(prediction.predictions()).hasSize(3);
        assertThat(prediction.deploymentEligible()).isFalse();

        SteelShadowLabelBatchRequest labels = new SteelShadowLabelBatchRequest(
                "steel-shadow-label-batch-v1",
                "e2e-label-001",
                null,
                List.of(new Label(
                        "E2E-H1",
                        "불량",
                        OffsetDateTime.now(ZoneOffset.UTC)
                ))
        );
        SteelShadowLabelResponse firstLabel = restTemplate.postForObject(
                "/api/steel-shadow/labels", labels, SteelShadowLabelResponse.class
        );
        SteelShadowLabelResponse secondLabel = restTemplate.postForObject(
                "/api/steel-shadow/labels", labels, SteelShadowLabelResponse.class
        );

        assertThat(firstLabel).isNotNull();
        assertThat(secondLabel.batchId()).isEqualTo(firstLabel.batchId());
        assertThat(firstLabel.shadowStatus()).isEqualTo("COLLECTING");
        assertThat(firstLabel.deploymentEligible()).isFalse();

        SteelShadowStatusResponse status = restTemplate.getForObject(
                "/api/steel-shadow/status", SteelShadowStatusResponse.class
        );
        assertThat(status).isNotNull();
        assertThat(status.status()).isEqualTo("COLLECTING");
        assertThat(status.predictionBatches()).isEqualTo(1);
        assertThat(status.labelBatches()).isEqualTo(1);
        assertThat(status.deploymentEligible()).isFalse();

        assertThat(committedCount("prediction_batches")).isEqualTo(1);
        assertThat(committedCount("label_batches")).isEqualTo(1);
    }

    private static SteelShadowFeatureBatchRequest featureRequest() {
        OffsetDateTime availableAt = OffsetDateTime.now(ZoneOffset.UTC).minusSeconds(2);
        return new SteelShadowFeatureBatchRequest(
                "steel-shadow-feature-batch-v1",
                "e2e-feature-001",
                List.of(new Coil(
                        "E2E-C1",
                        "1",
                        "E2E-H1",
                        availableAt.toLocalDate(),
                        availableAt,
                        new ProcessFeatures(
                                "1제강", "GRADE-A", "USE-A", 0.0, 0.0, 0.0, 0.0,
                                "CC-A", 1500.0, 50.0, "SLAB-A", "HSHS", "1호기", "A",
                                700.0, 10.0, 5.0, 0.0, 1100.0, 1250.0, 1250.0,
                                10.0, 20.0, 10.0, 1.0, 2.0, 1248.0, 950.0, 1.0, 1250.0
                        )
                ))
        );
    }

    private static long committedCount(String directory) throws IOException {
        try (var paths = Files.list(outputDir.resolve(directory))) {
            return paths.filter(path -> path.getFileName().toString().endsWith(".metadata.json"))
                    .count();
        }
    }

    private static void startSidecar() {
        try {
            Path projectRoot = locateProjectRoot();
            temporaryRoot = Files.createTempDirectory("steel-shadow-java-e2e-");
            outputDir = temporaryRoot.resolve("shadow_output");
            sidecarPort = availablePort();
            Path sidecarLog = temporaryRoot.resolve("sidecar.log");
            sidecar = new ProcessBuilder(
                    "python3",
                    "-m",
                    "training.steel.shadow_api",
                    "--host", "127.0.0.1",
                    "--port", Integer.toString(sidecarPort),
                    "--registry",
                    projectRoot.resolve("training/steel/shadow_registry/registry.json").toString(),
                    "--output-dir", outputDir.toString()
            )
                    .directory(projectRoot.toFile())
                    .redirectErrorStream(true)
                    .redirectOutput(sidecarLog.toFile())
                    .start();
            waitUntilHealthy(sidecarLog);
        } catch (Exception exception) {
            throw new IllegalStateException("failed to start Python Steel Shadow sidecar", exception);
        }
    }

    private static Path locateProjectRoot() {
        Path current = Path.of(System.getProperty("user.dir"))
                .toAbsolutePath()
                .normalize();
        for (Path candidate = current; candidate != null; candidate = candidate.getParent()) {
            if (Files.isRegularFile(candidate.resolve("training/steel/shadow_api.py"))) {
                return candidate;
            }
        }
        throw new IllegalStateException(
                "could not locate training/steel/shadow_api.py from " + current
        );
    }

    private static int availablePort() throws IOException {
        try (ServerSocket socket = new ServerSocket(0)) {
            return socket.getLocalPort();
        }
    }

    private static void waitUntilHealthy(Path sidecarLog) throws Exception {
        HttpClient client = HttpClient.newBuilder()
                .connectTimeout(Duration.ofSeconds(1))
                .build();
        HttpRequest health = HttpRequest.newBuilder()
                .uri(URI.create("http://127.0.0.1:" + sidecarPort + "/health"))
                .timeout(Duration.ofSeconds(2))
                .GET()
                .build();
        long deadline = System.nanoTime() + Duration.ofSeconds(30).toNanos();
        while (System.nanoTime() < deadline) {
            if (!sidecar.isAlive()) {
                throw new IllegalStateException(
                        "sidecar exited during startup: " + Files.readString(sidecarLog)
                );
            }
            try {
                HttpResponse<String> response = client.send(
                        health, HttpResponse.BodyHandlers.ofString()
                );
                if (response.statusCode() == 200 && response.body().contains("\"status\":\"UP\"")) {
                    return;
                }
            } catch (IOException ignored) {
                // Retry only during bounded startup readiness probing.
            }
            Thread.sleep(50);
        }
        throw new IllegalStateException(
                "sidecar did not become healthy: " + Files.readString(sidecarLog)
        );
    }
}
