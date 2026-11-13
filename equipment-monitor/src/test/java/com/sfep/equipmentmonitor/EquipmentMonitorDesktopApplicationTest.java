package com.sfep.equipmentmonitor;

import com.sfep.equipmentmonitor.bootstrap.MonitorLaunchException;
import com.sfep.equipmentmonitor.bootstrap.MonitorLaunchPreflight;
import com.sfep.equipmentmonitor.bootstrap.RuntimeProbe;
import com.sfep.equipmentmonitor.bundle.BundleLoadException;
import com.sfep.equipmentmonitor.ui.DesktopMonitorSession;
import com.sfep.equipmentmonitor.ui.DesktopMonitorWindow;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.ValueSource;
import org.springframework.boot.ApplicationRunner;
import org.springframework.boot.WebApplicationType;
import org.springframework.boot.test.context.runner.ApplicationContextRunner;
import org.springframework.test.util.ReflectionTestUtils;

import java.nio.file.Files;
import java.nio.file.Path;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

class EquipmentMonitorDesktopApplicationTest {
    private static final String OPTION = "--sfep.equipment-monitor.bundle-dir=";

    @TempDir
    Path temporary;

    @Test
    void configuresJava21DesktopApplicationAsNonWebAndNonHeadless() {
        var application = EquipmentMonitorDesktopApplication.application();

        assertThat(application.getWebApplicationType()).isEqualTo(WebApplicationType.NONE);
        assertThat(ReflectionTestUtils.getField(application, "headless")).isEqualTo(false);
    }

    @Test
    void acceptsExactlyOneAbsoluteBundleDirectoryOnJava21Desktop() {
        Path expected = Path.of("/private/tmp/sfep-golden-bundle");

        Path actual = MonitorLaunchPreflight.validate(
                new String[]{OPTION + expected},
                new RuntimeProbe(21, true, false));

        assertThat(actual).isEqualTo(expected);
    }

    @Test
    void failsClosedForMissingRelativeDuplicateOrBlankBundleDirectory() {
        RuntimeProbe validRuntime = new RuntimeProbe(21, true, false);

        assertCode("BUNDLE_DIR_REQUIRED", () ->
                MonitorLaunchPreflight.validate(new String[0], validRuntime));
        assertCode("BUNDLE_DIR_NOT_ABSOLUTE", () ->
                MonitorLaunchPreflight.validate(new String[]{OPTION + "relative/bundle"}, validRuntime));
        assertCode("BUNDLE_DIR_REQUIRED", () ->
                MonitorLaunchPreflight.validate(new String[]{OPTION}, validRuntime));
        assertCode("BUNDLE_DIR_DUPLICATE", () ->
                MonitorLaunchPreflight.validate(
                        new String[]{OPTION + "/one", OPTION + "/two"}, validRuntime));
    }

    @Test
    void failsClosedForWrongJavaMissingDesktopOrHeadlessEnvironment() {
        String[] args = {OPTION + "/private/tmp/bundle"};

        assertCode("JAVA_21_REQUIRED", () ->
                MonitorLaunchPreflight.validate(args, new RuntimeProbe(20, true, false)));
        assertCode("JAVA_DESKTOP_REQUIRED", () ->
                MonitorLaunchPreflight.validate(args, new RuntimeProbe(21, false, false)));
        assertCode("HEADLESS_ENVIRONMENT", () ->
                MonitorLaunchPreflight.validate(args, new RuntimeProbe(21, true, true)));
    }

    @ParameterizedTest
    @ValueSource(strings = {"v1", "v2"})
    void managesOneLegacyReplaySessionAndWindowWhileDeferringDisplayToApplicationRunner(String version)
            throws Exception {
        Path bundle = runtimeGoldenBundle(version);

        new ApplicationContextRunner()
                .withUserConfiguration(EquipmentMonitorDesktopApplication.class)
                .withPropertyValues("sfep.equipment-monitor.bundle-dir=" + bundle)
                .run(context -> {
                    assertThat(context).hasNotFailed();
                    assertThat(context).hasSingleBean(DesktopMonitorSession.class);
                    assertThat(context).hasSingleBean(DesktopMonitorWindow.class);
                    assertThat(context).hasSingleBean(ApplicationRunner.class);
                    assertThat(context.getBean(DesktopMonitorSession.class).metadata().replayRows())
                            .isEqualTo(95);
                    assertThat(context.getBean(DesktopMonitorSession.class).metadata().qualityModel()).isNull();
                });
    }

    @Test
    void opensTheVerifiedV3ModelDuringSpringStartup() throws Exception {
        Path bundle = runtimeGoldenBundle("v3");
        new ApplicationContextRunner()
                .withUserConfiguration(EquipmentMonitorDesktopApplication.class)
                .withPropertyValues("sfep.equipment-monitor.bundle-dir=" + bundle)
                .run(context -> {
                    assertThat(context).hasNotFailed();
                    var model = context.getBean(DesktopMonitorSession.class).metadata().qualityModel();
                    assertThat(model).isNotNull();
                    assertThat(model.qualification()).isEqualTo("QUALIFIED");
                });
    }

    @Test
    void rejectsV3ModelDigestMismatchDuringSpringStartup() throws Exception {
        Path bundle = runtimeGoldenBundle("v3");
        Path model = bundle.resolve("quality_model.onnx");
        byte[] bytes = Files.readAllBytes(model);
        bytes[bytes.length / 2] ^= 1;
        Files.write(model, bytes);

        new ApplicationContextRunner()
                .withUserConfiguration(EquipmentMonitorDesktopApplication.class)
                .withPropertyValues("sfep.equipment-monitor.bundle-dir=" + bundle)
                .run(context -> assertThat(context).getFailure()
                        .hasRootCauseInstanceOf(BundleLoadException.class)
                        .hasStackTraceContaining("ARTIFACT_HASH_MISMATCH"));
    }

    private Path runtimeGoldenBundle(String version) throws Exception {
        Path source = Path.of(System.getProperty("sfep.repo-root"))
                .resolve("contracts/equipment-monitor").resolve(version).resolve("golden-bundle");
        Path destination = Files.createDirectory(temporary.toRealPath().resolve("runtime-bundle"));
        try (var files = Files.list(source)) {
            for (Path file : files.toList()) {
                if (file.getFileName().toString().equals("expected_alerts.json")) continue;
                Files.copy(file, destination.resolve(file.getFileName()));
            }
        }
        return destination;
    }

    private static void assertCode(String code, Runnable action) {
        assertThatThrownBy(action::run)
                .isInstanceOf(MonitorLaunchException.class)
                .hasMessageStartingWith(code + ":");
    }
}
