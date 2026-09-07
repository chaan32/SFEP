package com.sfep.equipmentmonitor.bundle;

import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;

import java.nio.file.Files;
import java.nio.file.Path;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

class BundlePathSafetyTest {
    @TempDir
    Path temporary;

    @Test
    void acceptsOnlyTheFixedSevenRegularDirectChildren() throws Exception {
        Path physical = temporary.toRealPath();
        Path bundle = GoldenBundleFixture.copyTo(physical.resolve("bundle"));

        SafeBundleLayout layout = BundleLayout.preflight(bundle.toAbsolutePath());

        assertThat(layout.root()).isEqualTo(bundle.toRealPath());
        assertThat(layout.files()).hasSize(7);
        assertThat(layout.files().keySet()).containsExactlyInAnyOrderElementsOf(GoldenBundleFixture.BUNDLE_FILES);
    }

    @Test
    void rejectsRootSymlinkChildSymlinkDirectoryAndUnexpectedChild() throws Exception {
        Path physical = temporary.toRealPath();
        Path bundle = GoldenBundleFixture.copyTo(physical.resolve("bundle"));
        Path rootLink = physical.resolve("root-link");
        Files.createSymbolicLink(rootLink, bundle);
        assertCode("BUNDLE_ROOT_UNSAFE", () -> BundleLayout.preflight(rootLink.toAbsolutePath()));

        Path replay = bundle.resolve("replay_events.csv");
        Path external = physical.resolve("external.csv");
        Files.move(replay, external);
        Files.createSymbolicLink(replay, external);
        assertCode("BUNDLE_CHILD_UNSAFE", () -> BundleLayout.preflight(bundle));

        Files.delete(replay);
        Files.createDirectory(replay);
        assertCode("BUNDLE_CHILD_UNSAFE", () -> BundleLayout.preflight(bundle));

        Files.delete(replay);
        Files.copy(external, replay);
        Files.writeString(bundle.resolve("unexpected.txt"), "unexpected");
        assertCode("BUNDLE_INVENTORY_MISMATCH", () -> BundleLayout.preflight(bundle));
    }

    @Test
    void validatesEveryPathShapeBeforeOpeningEvenTheManifestBytes() throws Exception {
        Path physical = temporary.toRealPath();
        Path bundle = GoldenBundleFixture.copyTo(physical.resolve("bundle"));
        Files.writeString(bundle.resolve("bundle_manifest.json"), "not-json");
        Path original = bundle.resolve("analysis_summary.json");
        Path external = physical.resolve("summary.json");
        Files.move(original, external);
        Files.createSymbolicLink(original, external);

        assertCode("BUNDLE_CHILD_UNSAFE", () -> new BundleLoader().load(bundle));
    }

    @Test
    void rejectsAReplacedParentEvenWhenEveryReplacementChildIsTheSameHardLinkedFile() throws Exception {
        Path physical = temporary.toRealPath();
        Path bundle = GoldenBundleFixture.copyTo(physical.resolve("bundle"));
        SafeBundleLayout layout = BundleLayout.preflight(bundle);

        Path retained = physical.resolve("retained");
        Files.move(bundle, retained);
        Files.createDirectory(bundle);
        for (String name : GoldenBundleFixture.BUNDLE_FILES) {
            Files.createLink(bundle.resolve(name), retained.resolve(name));
        }

        assertCode("BUNDLE_FILE_CHANGED", () ->
                layout.file("bundle_manifest.json").readAllBytes("BUNDLE_FILE_CHANGED"));
    }

    private static void assertCode(String code, ThrowingAction action) {
        assertThatThrownBy(action::run)
                .isInstanceOf(BundleLoadException.class)
                .hasMessageStartingWith(code + ":");
    }

    @FunctionalInterface
    private interface ThrowingAction {
        void run() throws Exception;
    }
}
