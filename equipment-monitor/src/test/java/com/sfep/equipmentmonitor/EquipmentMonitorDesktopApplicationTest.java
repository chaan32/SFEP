package com.sfep.equipmentmonitor;

import com.sfep.equipmentmonitor.bootstrap.MonitorLaunchException;
import com.sfep.equipmentmonitor.bootstrap.MonitorLaunchPreflight;
import com.sfep.equipmentmonitor.bootstrap.RuntimeProbe;
import com.sfep.equipmentmonitor.ui.DesktopMonitorSession;
import com.sfep.equipmentmonitor.ui.DesktopMonitorWindow;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;
import org.springframework.boot.ApplicationRunner;
import org.springframework.boot.WebApplicationType;
import org.springframework.boot.test.context.runner.ApplicationContextRunner;
import org.springframework.test.util.ReflectionTestUtils;

import java.nio.file.Files;
import java.nio.file.Path;
import java.util.List;

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

    @Test
    void managesOneReplaySessionAndWindowWhileDeferringDisplayToApplicationRunner() throws Exception {
        Path bundle = runtimeGoldenBundle();

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
                });
    }

    private Path runtimeGoldenBundle() throws Exception {
        Path source = Path.of(System.getProperty("sfep.repo-root"))
                .resolve("contracts/equipment-monitor/v1/golden-bundle");
        Path destination = Files.createDirectory(temporary.toRealPath().resolve("runtime-bundle"));
        for (String name : List.of(
                "bundle_manifest.json",
                "analysis_config.json",
                "producer_runtime.json",
                "equipment_operating_ranges.json",
                "quality_risk_intervals.json",
                "replay_events.csv",
                "analysis_summary.json")) {
            Files.copy(source.resolve(name), destination.resolve(name));
        }
        return destination;
    }

    private static void assertCode(String code, Runnable action) {
        assertThatThrownBy(action::run)
                .isInstanceOf(MonitorLaunchException.class)
                .hasMessageStartingWith(code + ":");
    }
}
