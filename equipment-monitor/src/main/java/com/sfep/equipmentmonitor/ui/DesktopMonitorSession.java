package com.sfep.equipmentmonitor.ui;

import com.sfep.equipmentmonitor.bundle.LoadedBundle;
import com.sfep.equipmentmonitor.replay.ReplayController;
import com.sfep.equipmentmonitor.replay.ReplayControllerState;
import com.sfep.equipmentmonitor.replay.ReplayCursor;
import com.sfep.equipmentmonitor.replay.ReplaySpeed;
import com.sfep.equipmentmonitor.replay.ReplayUnit;
import com.sfep.equipmentmonitor.risk.RiskDefinitionCompiler;
import com.sfep.equipmentmonitor.state.HistoricalMonitor;
import com.sfep.equipmentmonitor.state.MonitorSnapshot;
import com.sfep.equipmentmonitor.state.MonitorUpdate;

import java.util.Objects;
import java.util.function.Consumer;

/** Owns one verified replay cursor, its historical monitor, and their lifecycle. */
public final class DesktopMonitorSession implements ReplayControl, AutoCloseable {
    private final MonitorDashboardMetadata metadata;
    private final HistoricalMonitor historicalMonitor;
    private final ReplayController replayController;

    public DesktopMonitorSession(LoadedBundle bundle) {
        Objects.requireNonNull(bundle, "bundle");
        RiskDefinitionCompiler compiler = new RiskDefinitionCompiler();
        this.historicalMonitor = new HistoricalMonitor(
                bundle.bundleId(),
                compiler.compileRanges(bundle.ranges()),
                compiler.compileRules(bundle.rules()));
        this.replayController = new ReplayController(
                ReplayCursor.open(bundle.replay()), historicalMonitor::accept);
        this.metadata = MonitorDashboardMetadata.from(bundle);
    }

    public MonitorDashboardMetadata metadata() {
        return metadata;
    }

    public AutoCloseable addUpdateListener(Consumer<MonitorUpdate> listener) {
        return historicalMonitor.addListener(listener);
    }

    public MonitorSnapshot snapshot() {
        return historicalMonitor.snapshot();
    }

    @Override
    public void start() {
        replayController.start();
    }

    @Override
    public void pause() {
        replayController.pause();
    }

    @Override
    public void resume() {
        replayController.resume();
    }

    @Override
    public void step() {
        replayController.step();
    }

    @Override
    public void stop() {
        replayController.stop();
    }

    @Override
    public void advanceToNextDate() {
        replayController.advanceToNextDate();
    }

    @Override
    public void setSpeed(ReplaySpeed speed) {
        replayController.setSpeed(speed);
    }

    @Override
    public void reportUiFailure(ReplayUnit unit, Throwable error) {
        replayController.reportFailure(unit, error);
    }

    @Override
    public ReplayControllerState state() {
        return replayController.state();
    }

    @Override
    public void close() {
        replayController.close();
    }
}
