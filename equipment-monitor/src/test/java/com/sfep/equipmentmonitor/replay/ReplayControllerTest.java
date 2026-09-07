package com.sfep.equipmentmonitor.replay;

import com.fasterxml.jackson.databind.node.JsonNodeFactory;
import org.junit.jupiter.api.Test;

import java.time.Duration;
import java.util.ArrayDeque;
import java.util.ArrayList;
import java.util.List;
import java.util.Optional;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.TimeUnit;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.awaitility.Awaitility.await;

class ReplayControllerTest {
    @Test
    void stepDeliversExactlyOneUnitThenPauses() {
        StubCursor cursor = new StubCursor(unit("B1", "S1"), unit("B2", "S2"));
        List<ReplayUnit> delivered = new ArrayList<>();

        try (ReplayController controller = new ReplayController(cursor, delivered::add, speed -> { })) {
            controller.step();

            awaitState(controller, ReplayStatus.PAUSED, 1);
            assertThat(delivered).extracting(ReplayUnit::batchId).containsExactly("B1");
            assertThat(controller.state().paused()).isTrue();
        }
    }

    @Test
    void steppingTheLastUnitCompletesWithoutASecondStep() {
        StubCursor cursor = new StubCursor(unit("B1", "S1"));
        List<ReplayUnit> delivered = new ArrayList<>();

        try (ReplayController controller = new ReplayController(cursor, delivered::add, speed -> { })) {
            controller.step();

            awaitState(controller, ReplayStatus.COMPLETED, 1);
            assertThat(delivered).extracting(ReplayUnit::batchId).containsExactly("B1");
        }
    }

    @Test
    void advanceToNextDateStopsOnTheFirstUnitOfTheFollowingDate() {
        StubCursor cursor = new StubCursor(
                unit("B1", "S1", "2025-01-01"),
                unit("B2", "S2", "2025-01-01"),
                unit("B3", "S3", "2025-01-02"),
                unit("B4", "S4", "2025-01-02"));
        List<ReplayUnit> delivered = new ArrayList<>();

        try (ReplayController controller = new ReplayController(cursor, delivered::add, speed -> { })) {
            controller.step();
            awaitState(controller, ReplayStatus.PAUSED, 1);

            controller.advanceToNextDate();
            awaitState(controller, ReplayStatus.PAUSED, 3);

            assertThat(delivered).extracting(ReplayUnit::batchId)
                    .containsExactly("B1", "B2", "B3");
            assertThat(controller.state().currentReplayDate()).isEqualTo("2025-01-02");
        }
    }

    @Test
    void advanceToNextDateFromTheBeginningStopsAtTheFirstAvailableUnit() {
        StubCursor cursor = new StubCursor(
                unit("B1", "S1", "2025-01-01"),
                unit("B2", "S2", "2025-01-01"));
        List<ReplayUnit> delivered = new ArrayList<>();

        try (ReplayController controller = new ReplayController(cursor, delivered::add, speed -> { })) {
            controller.advanceToNextDate();
            awaitState(controller, ReplayStatus.PAUSED, 1);

            assertThat(delivered).extracting(ReplayUnit::batchId).containsExactly("B1");
            assertThat(controller.state().currentReplayDate()).isEqualTo("2025-01-01");
        }
    }

    @Test
    void advanceToNextDateCompletesWhenNoFollowingDateExists() {
        StubCursor cursor = new StubCursor(
                unit("B1", "S1", "2025-01-01"),
                unit("B2", "S2", "2025-01-01"));
        List<ReplayUnit> delivered = new ArrayList<>();

        try (ReplayController controller = new ReplayController(cursor, delivered::add, speed -> { })) {
            controller.step();
            awaitState(controller, ReplayStatus.PAUSED, 1);

            controller.advanceToNextDate();
            awaitState(controller, ReplayStatus.COMPLETED, 2);

            assertThat(delivered).extracting(ReplayUnit::batchId).containsExactly("B1", "B2");
            assertThat(controller.state().currentReplayDate()).isEqualTo("2025-01-01");
        }
    }

    @Test
    void pauseWhileCursorIsReadingDoesNotLeakThePendingUnitToTheConsumer() throws Exception {
        BlockingCursor cursor = new BlockingCursor(unit("B1", "S1"));
        List<ReplayUnit> delivered = new ArrayList<>();

        try (ReplayController controller = new ReplayController(cursor, delivered::add, speed -> { })) {
            controller.start();
            assertThat(cursor.entered.await(5, TimeUnit.SECONDS)).isTrue();
            controller.pause();
            cursor.release.countDown();

            awaitState(controller, ReplayStatus.PAUSED, 0);
            assertThat(delivered).isEmpty();

            controller.resume();
            awaitState(controller, ReplayStatus.COMPLETED, 1);
            assertThat(delivered).extracting(ReplayUnit::batchId).containsExactly("B1");
        }
    }

    @Test
    void pauseAndStopTakeEffectAtUnitBoundariesAndStartResumesFromTheCursor() throws Exception {
        StubCursor cursor = new StubCursor(unit("B1", "S1"), unit("B2", "S2"), unit("B3", "S3"));
        List<ReplayUnit> delivered = new ArrayList<>();
        CountDownLatch firstEntered = new CountDownLatch(1);
        CountDownLatch releaseFirst = new CountDownLatch(1);
        ReplayUnitConsumer consumer = unit -> {
            delivered.add(unit);
            if (unit.batchId().equals("B1")) {
                firstEntered.countDown();
                assertThat(releaseFirst.await(5, TimeUnit.SECONDS)).isTrue();
            }
        };

        try (ReplayController controller = new ReplayController(cursor, consumer, speed -> { })) {
            controller.start();
            assertThat(firstEntered.await(5, TimeUnit.SECONDS)).isTrue();
            controller.pause();
            assertThat(controller.state().status()).isEqualTo(ReplayStatus.RUNNING);
            releaseFirst.countDown();
            awaitState(controller, ReplayStatus.PAUSED, 1);

            controller.setSpeed(ReplaySpeed.X100);
            controller.resume();
            awaitState(controller, ReplayStatus.COMPLETED, 3);

            assertThat(delivered).extracting(ReplayUnit::batchId).containsExactly("B1", "B2", "B3");
            assertThat(controller.state().speed()).isEqualTo(ReplaySpeed.X100);
            assertThat(controller.state().paused()).isTrue();
        }
    }

    @Test
    void stopAtABoundaryCanBeStartedAgain() throws Exception {
        StubCursor cursor = new StubCursor(unit("B1", "S1"), unit("B2", "S2"));
        CountDownLatch entered = new CountDownLatch(1);
        CountDownLatch release = new CountDownLatch(1);
        List<ReplayUnit> delivered = new ArrayList<>();

        try (ReplayController controller = new ReplayController(cursor, unit -> {
            delivered.add(unit);
            if (delivered.size() == 1) {
                entered.countDown();
                release.await(5, TimeUnit.SECONDS);
            }
        }, speed -> { })) {
            controller.start();
            assertThat(entered.await(5, TimeUnit.SECONDS)).isTrue();
            controller.stop();
            release.countDown();
            awaitState(controller, ReplayStatus.STOPPED, 1);

            controller.start();
            awaitState(controller, ReplayStatus.COMPLETED, 2);
        }
        assertThat(delivered).hasSize(2);
    }

    @Test
    void consumerFailureBecomesPausedErrorAndCloseShutsTheCursor() {
        StubCursor cursor = new StubCursor(unit("B1", "S1"));
        ReplayController controller = new ReplayController(
                cursor,
                unit -> { throw new IllegalStateException("boom"); },
                speed -> { });

        controller.start();
        awaitState(controller, ReplayStatus.ERROR, 0);

        assertThat(controller.state().paused()).isTrue();
        assertThat(controller.state().errorMessage()).contains("boom");
        assertThat(controller.state().failureContext()).isEqualTo(
                new ReplayFailureContext(
                        "B1", "S1", "2025-01-01", 1, List.of("material-B1")));
        assertThat(controller.state().statusHistory()).containsExactly(
                ReplayStatus.STOPPED, ReplayStatus.RUNNING, ReplayStatus.ERROR);
        controller.close();
        assertThat(cursor.closed).isTrue();
        assertThat(controller.state().status()).isEqualTo(ReplayStatus.CLOSED);
        assertThatThrownBy(controller::start).isInstanceOf(IllegalStateException.class);
    }

    @Test
    void externallyReportedUiFailureStopsReplayAndRetainsItsUnitContext() {
        StubCursor cursor = new StubCursor(unit("B1", "S1"), unit("B2", "S2"));
        ReplayController controller = new ReplayController(cursor, ignored -> { }, speed -> { });

        controller.reportFailure(unit("UI", "SCREEN"), new IllegalStateException("render failed"));

        assertThat(controller.state().status()).isEqualTo(ReplayStatus.ERROR);
        assertThat(controller.state().paused()).isTrue();
        assertThat(controller.state().errorMessage()).contains("render failed");
        assertThat(controller.state().failureContext()).extracting(
                ReplayFailureContext::batchId,
                ReplayFailureContext::batchStep,
                ReplayFailureContext::materialKeys)
                .containsExactly("UI", "SCREEN", List.of("material-UI"));
        assertThat(controller.state().statusHistory()).containsExactly(
                ReplayStatus.STOPPED, ReplayStatus.ERROR);

        controller.close();
        assertThat(controller.state().statusHistory()).containsExactly(
                ReplayStatus.STOPPED, ReplayStatus.ERROR, ReplayStatus.CLOSED);
    }

    @Test
    void uiFailureReportedDuringDeliveryRemainsErrorAtTheWorkerBoundary() throws Exception {
        ReplayUnit affected = unit("B1", "S1");
        StubCursor cursor = new StubCursor(affected, unit("B2", "S2"));
        CountDownLatch entered = new CountDownLatch(1);
        CountDownLatch release = new CountDownLatch(1);

        try (ReplayController controller = new ReplayController(cursor, ignored -> {
            entered.countDown();
            assertThat(release.await(5, TimeUnit.SECONDS)).isTrue();
        }, speed -> { })) {
            controller.start();
            assertThat(entered.await(5, TimeUnit.SECONDS)).isTrue();

            controller.reportFailure(affected, new IllegalStateException("EDT failed"));
            release.countDown();

            awaitState(controller, ReplayStatus.ERROR, 1);
            assertThat(controller.state().statusHistory()).containsExactly(
                    ReplayStatus.STOPPED, ReplayStatus.RUNNING, ReplayStatus.ERROR);
            assertThat(controller.state().failureContext().batchId()).isEqualTo("B1");
        }
    }

    private static void awaitState(ReplayController controller, ReplayStatus status, long delivered) {
        await().atMost(Duration.ofSeconds(5)).untilAsserted(() -> {
            assertThat(controller.state().status()).isEqualTo(status);
            assertThat(controller.state().unitsDelivered()).isEqualTo(delivered);
        });
    }

    private static ReplayUnit unit(String batchId, String step) {
        return unit(batchId, step, "2025-01-01");
    }

    private static ReplayUnit unit(String batchId, String step, String replayDate) {
        ReplayEvent event = new ReplayEvent(
                "sfep-replay-events/v1", "bundle", "criteria", "event-" + batchId,
                replayDate, 1, "FURNACE_HOUR", batchId, "equipment-" + batchId,
                step, "HOUR_BUCKET", "material-" + batchId, "FURNACE", "1호기",
                "charge", "slab", null, null, JsonNodeFactory.instance.objectNode());
        return new ReplayUnit(
                batchId, step, replayDate, 1, "FURNACE_HOUR", "HOUR_BUCKET",
                List.of(event), false);
    }

    private static final class StubCursor implements ReplayUnitCursor {
        private final ArrayDeque<ReplayUnit> units;
        private boolean closed;

        private StubCursor(ReplayUnit... units) {
            this.units = new ArrayDeque<>(List.of(units));
        }

        @Override
        public synchronized Optional<ReplayUnit> nextUnit() {
            if (closed) {
                throw new IllegalStateException("closed");
            }
            return Optional.ofNullable(units.pollFirst());
        }

        @Override
        public synchronized boolean exhausted() {
            return units.isEmpty();
        }

        @Override
        public synchronized void close() {
            closed = true;
        }
    }

    private static final class BlockingCursor implements ReplayUnitCursor {
        private final ReplayUnit unit;
        private final CountDownLatch entered = new CountDownLatch(1);
        private final CountDownLatch release = new CountDownLatch(1);
        private boolean delivered;
        private boolean closed;

        private BlockingCursor(ReplayUnit unit) {
            this.unit = unit;
        }

        @Override
        public Optional<ReplayUnit> nextUnit() {
            entered.countDown();
            try {
                if (!release.await(5, TimeUnit.SECONDS)) {
                    throw new IllegalStateException("cursor was not released");
                }
            } catch (InterruptedException error) {
                Thread.currentThread().interrupt();
                throw new IllegalStateException(error);
            }
            synchronized (this) {
                if (closed || delivered) {
                    return Optional.empty();
                }
                delivered = true;
                return Optional.of(unit);
            }
        }

        @Override
        public synchronized boolean exhausted() {
            return delivered;
        }

        @Override
        public synchronized void close() {
            closed = true;
            release.countDown();
        }
    }
}
