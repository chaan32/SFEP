package com.sfep.sfep_server.desktop;

import com.sfep.sfep_server.dashboard.dto.DashboardSummaryResponse;
import com.sfep.sfep_server.dashboard.service.DashboardService;
import com.sfep.sfep_server.equipment.domain.EquipmentStatus;
import com.sfep.sfep_server.equipment.domain.EquipmentType;
import com.sfep.sfep_server.event.dto.SensorEventRequest;
import com.sfep.sfep_server.event.service.SensorEventService;

import javax.swing.BorderFactory;
import javax.swing.JButton;
import javax.swing.JFrame;
import javax.swing.JLabel;
import javax.swing.JPanel;
import javax.swing.JProgressBar;
import javax.swing.JScrollPane;
import javax.swing.JSpinner;
import javax.swing.JTextArea;
import javax.swing.SpinnerNumberModel;
import javax.swing.SwingWorker;
import javax.swing.Timer;
import java.awt.BorderLayout;
import java.awt.Dimension;
import java.awt.FlowLayout;
import java.awt.Font;
import java.awt.GridLayout;
import java.time.Instant;
import java.time.LocalTime;
import java.time.format.DateTimeFormatter;
import java.util.List;
import java.util.UUID;
import java.util.concurrent.ThreadLocalRandom;
import java.util.concurrent.TimeUnit;
import java.util.function.Consumer;

public class SfepDesktopFrame extends JFrame {

    private static final DateTimeFormatter TIME_FORMATTER = DateTimeFormatter.ofPattern("HH:mm:ss");

    private final SensorEventService sensorEventService;
    private final DashboardService dashboardService;

    private final JSpinner equipmentCountInput = new JSpinner(new SpinnerNumberModel(100, 1, 1000, 10));
    private final JSpinner eventsPerEquipmentInput = new JSpinner(new SpinnerNumberModel(10, 1, 1000, 10));
    private final JSpinner failureRateInput = new JSpinner(new SpinnerNumberModel(2, 0, 100, 1));
    private final JSpinner streamEquipmentCountInput = new JSpinner(new SpinnerNumberModel(100, 1, 1000, 10));
    private final JSpinner streamEventsPerSecondInput = new JSpinner(new SpinnerNumberModel(1, 1, 1000, 1));
    private final JSpinner streamDurationMinutesInput = new JSpinner(new SpinnerNumberModel(1, 1, 60, 1));
    private final JSpinner streamFailureRateInput = new JSpinner(new SpinnerNumberModel(2, 0, 100, 1));

    private final JButton startButton = new JButton("보내기");
    private final JButton streamStartButton = new JButton("모니터링 시작");
    private final JButton streamStopButton = new JButton("중지");
    private final JProgressBar progressBar = new JProgressBar(0, 100);
    private final JProgressBar streamProgressBar = new JProgressBar(0, 100);
    private final JLabel savedEventsLabel = new JLabel("0 / 0");
    private final JLabel elapsedLabel = new JLabel("0 ms");
    private final JLabel throughputLabel = new JLabel("0 events/sec");
    private final JLabel streamSavedEventsLabel = new JLabel("0 / 0");
    private final JLabel streamElapsedLabel = new JLabel("0 ms");
    private final JLabel streamTargetRateLabel = new JLabel("0 events/sec");
    private final JLabel streamThroughputLabel = new JLabel("0 events/sec");
    private final JLabel streamSuccessRateLabel = new JLabel("0%");
    private final JLabel streamBacklogLabel = new JLabel("0");
    private final JLabel equipmentLabel = new JLabel("0");
    private final JLabel warningLabel = new JLabel("0");
    private final JLabel criticalLabel = new JLabel("0");
    private final JLabel lastMinuteLabel = new JLabel("0");
    private final JTextArea logArea = new JTextArea();

    private SwingWorker<Void, ProgressSnapshot> currentWorker;
    private SwingWorker<Void, StreamSnapshot> streamWorker;

    public SfepDesktopFrame(
            SensorEventService sensorEventService,
            DashboardService dashboardService,
            int dashboardRefreshMs
    ) {
        super("SFEP Event Load Monitor");
        this.sensorEventService = sensorEventService;
        this.dashboardService = dashboardService;

        setDefaultCloseOperation(JFrame.DISPOSE_ON_CLOSE);
        setMinimumSize(new Dimension(1100, 780));
        setLocationByPlatform(true);

        add(buildRootPanel(), BorderLayout.CENTER);
        startButton.addActionListener(event -> startDirectWrite());
        streamStartButton.addActionListener(event -> startContinuousMonitoring());
        streamStopButton.addActionListener(event -> stopContinuousMonitoring());
        streamStopButton.setEnabled(false);

        Timer timer = new Timer(dashboardRefreshMs, event -> refreshDashboard());
        timer.start();
        refreshDashboard();
    }

    private JPanel buildRootPanel() {
        JPanel root = new JPanel(new BorderLayout(16, 16));
        root.setBorder(BorderFactory.createEmptyBorder(18, 18, 18, 18));
        root.add(buildInputPanel(), BorderLayout.NORTH);
        root.add(buildMetricPanel(), BorderLayout.CENTER);
        root.add(buildLogPanel(), BorderLayout.SOUTH);
        return root;
    }

    private JPanel buildInputPanel() {
        JPanel panel = new JPanel(new GridLayout(2, 1, 10, 10));
        panel.add(buildDirectInputPanel());
        panel.add(buildStreamInputPanel());
        return panel;
    }

    private JPanel buildDirectInputPanel() {
        JPanel panel = new JPanel(new FlowLayout(FlowLayout.LEFT, 12, 8));
        panel.setBorder(BorderFactory.createTitledBorder("단발 저장 실험 설정"));
        panel.add(new JLabel("설비 수"));
        panel.add(equipmentCountInput);
        panel.add(new JLabel("설비당 이벤트 수"));
        panel.add(eventsPerEquipmentInput);
        panel.add(new JLabel("CRITICAL 비율(%)"));
        panel.add(failureRateInput);
        panel.add(startButton);
        return panel;
    }

    private JPanel buildStreamInputPanel() {
        JPanel panel = new JPanel(new FlowLayout(FlowLayout.LEFT, 12, 8));
        panel.setBorder(BorderFactory.createTitledBorder("지속 유입 모니터링 설정"));
        panel.add(new JLabel("설비 수"));
        panel.add(streamEquipmentCountInput);
        panel.add(new JLabel("설비당 초당 이벤트"));
        panel.add(streamEventsPerSecondInput);
        panel.add(new JLabel("기간(분)"));
        panel.add(streamDurationMinutesInput);
        panel.add(new JLabel("CRITICAL 비율(%)"));
        panel.add(streamFailureRateInput);
        panel.add(streamStartButton);
        panel.add(streamStopButton);
        return panel;
    }

    private JPanel buildMetricPanel() {
        JPanel panel = new JPanel(new GridLayout(3, 1, 12, 12));
        panel.add(buildCurrentRunPanel());
        panel.add(buildContinuousRunPanel());
        panel.add(buildDashboardPanel());
        return panel;
    }

    private JPanel buildCurrentRunPanel() {
        JPanel panel = new JPanel(new GridLayout(2, 4, 12, 12));
        panel.setBorder(BorderFactory.createTitledBorder("현재 실행 처리율"));
        panel.add(metric("저장 이벤트", savedEventsLabel));
        panel.add(metric("진행률", progressBar));
        panel.add(metric("소요 시간", elapsedLabel));
        panel.add(metric("초당 처리율", throughputLabel));
        return panel;
    }

    private JPanel buildContinuousRunPanel() {
        JPanel panel = new JPanel(new GridLayout(2, 4, 12, 12));
        panel.setBorder(BorderFactory.createTitledBorder("지속 유입 처리 안정성"));
        panel.add(metric("처리 이벤트", streamSavedEventsLabel));
        panel.add(metric("진행률", streamProgressBar));
        panel.add(metric("경과 시간", streamElapsedLabel));
        panel.add(metric("목표 유입률", streamTargetRateLabel));
        panel.add(metric("실제 처리율", streamThroughputLabel));
        panel.add(metric("목표 대비 처리율", streamSuccessRateLabel));
        panel.add(metric("미처리 목표 이벤트", streamBacklogLabel));
        return panel;
    }

    private JPanel buildDashboardPanel() {
        JPanel panel = new JPanel(new GridLayout(2, 4, 12, 12));
        panel.setBorder(BorderFactory.createTitledBorder("DB 저장 상태"));
        panel.add(metric("전체 설비", equipmentLabel));
        panel.add(metric("최근 1분 이벤트", lastMinuteLabel));
        panel.add(metric("WARNING 이벤트", warningLabel));
        panel.add(metric("CRITICAL 이벤트", criticalLabel));
        return panel;
    }

    private JPanel metric(String title, java.awt.Component value) {
        JPanel panel = new JPanel(new BorderLayout(4, 4));
        JLabel titleLabel = new JLabel(title);
        titleLabel.setFont(titleLabel.getFont().deriveFont(Font.BOLD));
        panel.add(titleLabel, BorderLayout.NORTH);
        panel.add(value, BorderLayout.CENTER);
        panel.setBorder(BorderFactory.createEmptyBorder(8, 8, 8, 8));
        return panel;
    }

    private JScrollPane buildLogPanel() {
        logArea.setEditable(false);
        logArea.setRows(10);
        logArea.setFont(Font.decode(Font.MONOSPACED));
        JScrollPane scrollPane = new JScrollPane(logArea);
        scrollPane.setBorder(BorderFactory.createTitledBorder("실행 로그"));
        return scrollPane;
    }

    private void startDirectWrite() {
        if (currentWorker != null && !currentWorker.isDone()) {
            appendLog("이미 실행 중인 작업이 있습니다.");
            return;
        }
        if (streamWorker != null && !streamWorker.isDone()) {
            appendLog("지속 유입 모니터링 중에는 단발 저장 실험을 실행할 수 없습니다.");
            return;
        }

        int equipmentCount = (Integer) equipmentCountInput.getValue();
        int eventsPerEquipment = (Integer) eventsPerEquipmentInput.getValue();
        int failureRatePercent = (Integer) failureRateInput.getValue();
        int totalEvents = equipmentCount * eventsPerEquipment;

        startButton.setEnabled(false);
        progressBar.setValue(0);
        savedEventsLabel.setText("0 / " + totalEvents);
        elapsedLabel.setText("0 ms");
        throughputLabel.setText("0 events/sec");
        appendLog("작업 시작: equipment=%d, eventsPerEquipment=%d, total=%d"
                .formatted(equipmentCount, eventsPerEquipment, totalEvents));

        currentWorker = new SwingWorker<>() {
            @Override
            protected Void doInBackground() {
                runDirectWrite(equipmentCount, eventsPerEquipment, failureRatePercent, totalEvents, this::publish);
                return null;
            }

            @Override
            protected void process(List<ProgressSnapshot> snapshots) {
                ProgressSnapshot latest = snapshots.get(snapshots.size() - 1);
                updateProgress(latest);
            }

            @Override
            protected void done() {
                startButton.setEnabled(true);
                refreshDashboard();
                appendLog("작업 종료");
            }
        };
        currentWorker.execute();
    }

    private void startContinuousMonitoring() {
        if (currentWorker != null && !currentWorker.isDone()) {
            appendLog("단발 저장 실험 중에는 지속 유입 모니터링을 실행할 수 없습니다.");
            return;
        }
        if (streamWorker != null && !streamWorker.isDone()) {
            appendLog("이미 지속 유입 모니터링이 실행 중입니다.");
            return;
        }

        int equipmentCount = (Integer) streamEquipmentCountInput.getValue();
        int eventsPerEquipmentPerSecond = (Integer) streamEventsPerSecondInput.getValue();
        int durationMinutes = (Integer) streamDurationMinutesInput.getValue();
        int failureRatePercent = (Integer) streamFailureRateInput.getValue();
        long targetRatePerSecond = (long) equipmentCount * eventsPerEquipmentPerSecond;
        long targetTotalEvents = targetRatePerSecond * durationMinutes * 60L;

        streamStartButton.setEnabled(false);
        streamStopButton.setEnabled(true);
        streamProgressBar.setValue(0);
        streamSavedEventsLabel.setText("0 / " + targetTotalEvents);
        streamElapsedLabel.setText("0 ms");
        streamTargetRateLabel.setText(targetRatePerSecond + " events/sec");
        streamThroughputLabel.setText("0 events/sec");
        streamSuccessRateLabel.setText("0%");
        streamBacklogLabel.setText("0");
        appendLog("지속 유입 모니터링 시작: equipment=%d, eventsPerEquipmentPerSecond=%d, durationMinutes=%d, targetRate=%d/sec, targetTotal=%d"
                .formatted(equipmentCount, eventsPerEquipmentPerSecond, durationMinutes, targetRatePerSecond, targetTotalEvents));

        streamWorker = new SwingWorker<>() {
            @Override
            protected Void doInBackground() {
                runContinuousWrite(
                        equipmentCount,
                        failureRatePercent,
                        targetRatePerSecond,
                        targetTotalEvents,
                        durationMinutes,
                        this::publish
                );
                return null;
            }

            @Override
            protected void process(List<StreamSnapshot> snapshots) {
                StreamSnapshot latest = snapshots.get(snapshots.size() - 1);
                updateStreamProgress(latest);
            }

            @Override
            protected void done() {
                streamStartButton.setEnabled(true);
                streamStopButton.setEnabled(false);
                refreshDashboard();
                appendLog(isCancelled() ? "지속 유입 모니터링 중지" : "지속 유입 모니터링 종료");
            }
        };
        streamWorker.execute();
    }

    private void stopContinuousMonitoring() {
        if (streamWorker != null && !streamWorker.isDone()) {
            streamWorker.cancel(true);
        }
    }

    private void runDirectWrite(
            int equipmentCount,
            int eventsPerEquipment,
            int failureRatePercent,
            int totalEvents,
            Consumer<ProgressSnapshot> progressPublisher
    ) {
        long startNanos = System.nanoTime();
        int savedEvents = 0;
        EquipmentType[] types = EquipmentType.values();
        int publishInterval = Math.max(1, totalEvents / 100);

        for (int equipmentIndex = 1; equipmentIndex <= equipmentCount; equipmentIndex++) {
            String equipmentId = "EQ-%04d".formatted(equipmentIndex);
            EquipmentType type = types[(equipmentIndex - 1) % types.length];
            for (int eventIndex = 0; eventIndex < eventsPerEquipment; eventIndex++) {
                sensorEventService.saveDirect(generateEvent(equipmentId, type, failureRatePercent));
                savedEvents++;

                if (savedEvents % publishInterval == 0 || savedEvents == totalEvents) {
                    progressPublisher.accept(snapshot(savedEvents, totalEvents, startNanos));
                }
            }
        }
    }

    private void runContinuousWrite(
            int equipmentCount,
            int failureRatePercent,
            long targetRatePerSecond,
            long targetTotalEvents,
            int durationMinutes,
            Consumer<StreamSnapshot> progressPublisher
    ) {
        long startNanos = System.nanoTime();
        long durationNanos = TimeUnit.MINUTES.toNanos(durationMinutes);
        long endNanos = startNanos + durationNanos;
        long processedEvents = 0;
        long failedEvents = 0;
        long lastPublishNanos = startNanos;
        EquipmentType[] types = EquipmentType.values();

        while (!Thread.currentThread().isInterrupted() && System.nanoTime() < endNanos) {
            long now = System.nanoTime();
            long elapsedNanos = now - startNanos;
            long targetGeneratedEvents = Math.min(
                    targetTotalEvents,
                    elapsedNanos * targetRatePerSecond / 1_000_000_000L
            );
            long attemptedEvents = processedEvents + failedEvents;

            if (attemptedEvents < targetGeneratedEvents) {
                int equipmentIndex = (int) (attemptedEvents % equipmentCount) + 1;
                String equipmentId = "EQ-%04d".formatted(equipmentIndex);
                EquipmentType type = types[(equipmentIndex - 1) % types.length];

                try {
                    sensorEventService.saveDirect(generateEvent(equipmentId, type, failureRatePercent));
                    processedEvents++;
                } catch (RuntimeException exception) {
                    failedEvents++;
                }
            } else {
                sleepQuietly(5);
            }

            if (now - lastPublishNanos >= 1_000_000_000L) {
                progressPublisher.accept(streamSnapshot(
                        processedEvents,
                        failedEvents,
                        targetTotalEvents,
                        targetGeneratedEvents,
                        startNanos,
                        durationNanos,
                        targetRatePerSecond
                ));
                lastPublishNanos = now;
            }
        }

        long finalElapsedNanos = Math.min(System.nanoTime() - startNanos, durationNanos);
        long finalTargetGeneratedEvents = Math.min(
                targetTotalEvents,
                finalElapsedNanos * targetRatePerSecond / 1_000_000_000L
        );
        progressPublisher.accept(streamSnapshot(
                processedEvents,
                failedEvents,
                targetTotalEvents,
                finalTargetGeneratedEvents,
                startNanos,
                durationNanos,
                targetRatePerSecond
        ));
    }

    private ProgressSnapshot snapshot(int savedEvents, int totalEvents, long startNanos) {
        long elapsedMs = (System.nanoTime() - startNanos) / 1_000_000;
        double seconds = Math.max(elapsedMs, 1) / 1000.0;
        double throughput = savedEvents / seconds;
        int progress = (int) Math.round(savedEvents * 100.0 / totalEvents);
        return new ProgressSnapshot(savedEvents, totalEvents, elapsedMs, throughput, progress);
    }

    private StreamSnapshot streamSnapshot(
            long processedEvents,
            long failedEvents,
            long targetTotalEvents,
            long targetGeneratedEvents,
            long startNanos,
            long durationNanos,
            long targetRatePerSecond
    ) {
        long elapsedNanos = System.nanoTime() - startNanos;
        long elapsedMs = elapsedNanos / 1_000_000;
        double seconds = Math.max(elapsedMs, 1) / 1000.0;
        double throughput = processedEvents / seconds;
        long attemptedEvents = processedEvents + failedEvents;
        long backlog = Math.max(0, targetGeneratedEvents - attemptedEvents);
        double successRate = targetGeneratedEvents == 0 ? 100.0 : processedEvents * 100.0 / targetGeneratedEvents;
        int progress = (int) Math.min(100, Math.round(elapsedNanos * 100.0 / durationNanos));
        return new StreamSnapshot(
                processedEvents,
                failedEvents,
                targetTotalEvents,
                elapsedMs,
                targetRatePerSecond,
                throughput,
                successRate,
                backlog,
                progress
        );
    }

    private void updateProgress(ProgressSnapshot snapshot) {
        savedEventsLabel.setText(snapshot.savedEvents() + " / " + snapshot.totalEvents());
        elapsedLabel.setText(snapshot.elapsedMs() + " ms");
        throughputLabel.setText("%.2f events/sec".formatted(snapshot.eventsPerSecond()));
        progressBar.setValue(snapshot.progressPercent());
    }

    private void updateStreamProgress(StreamSnapshot snapshot) {
        streamSavedEventsLabel.setText("%d / %d (failed %d)"
                .formatted(snapshot.processedEvents(), snapshot.targetTotalEvents(), snapshot.failedEvents()));
        streamElapsedLabel.setText(snapshot.elapsedMs() + " ms");
        streamTargetRateLabel.setText(snapshot.targetRatePerSecond() + " events/sec");
        streamThroughputLabel.setText("%.2f events/sec".formatted(snapshot.eventsPerSecond()));
        streamSuccessRateLabel.setText("%.2f%%".formatted(snapshot.successRatePercent()));
        streamBacklogLabel.setText(String.valueOf(snapshot.backlog()));
        streamProgressBar.setValue(snapshot.progressPercent());
    }

    private SensorEventRequest generateEvent(String equipmentId, EquipmentType type, int failureRatePercent) {
        ThreadLocalRandom random = ThreadLocalRandom.current();
        EquipmentStatus status = randomStatus(random, failureRatePercent);
        boolean failure = status == EquipmentStatus.FAILURE;
        boolean warning = status == EquipmentStatus.WARNING;

        double temperature = random.nextDouble(45.0, 72.0);
        double vibration = random.nextDouble(0.4, 4.2);
        double currentValue = random.nextDouble(20.0, 52.0);

        if (warning) {
            temperature = random.nextDouble(75.0, 88.0);
            vibration = random.nextDouble(5.0, 7.5);
            currentValue = random.nextDouble(60.0, 78.0);
        }
        if (failure) {
            temperature = random.nextDouble(90.0, 115.0);
            vibration = random.nextDouble(8.0, 13.0);
            currentValue = random.nextDouble(80.0, 110.0);
        }

        return new SensorEventRequest(
                UUID.randomUUID().toString(),
                equipmentId,
                type,
                status,
                round(temperature),
                round(vibration),
                round(random.nextDouble(650.0, 2400.0)),
                round(random.nextDouble(2.0, 9.5)),
                round(currentValue),
                Instant.now()
        );
    }

    private EquipmentStatus randomStatus(ThreadLocalRandom random, int failureRatePercent) {
        int roll = random.nextInt(100);
        if (roll < failureRatePercent) {
            return EquipmentStatus.FAILURE;
        }
        if (roll < failureRatePercent + 8) {
            return EquipmentStatus.WARNING;
        }
        if (roll < failureRatePercent + 18) {
            return EquipmentStatus.IDLE;
        }
        return EquipmentStatus.RUNNING;
    }

    private void sleepQuietly(long millis) {
        try {
            TimeUnit.MILLISECONDS.sleep(millis);
        } catch (InterruptedException exception) {
            Thread.currentThread().interrupt();
        }
    }

    private void refreshDashboard() {
        try {
            DashboardSummaryResponse summary = dashboardService.getSummary();
            equipmentLabel.setText(String.valueOf(summary.totalEquipment()));
            lastMinuteLabel.setText(String.valueOf(summary.eventsLastMinute()));
            warningLabel.setText(String.valueOf(summary.warningEvents()));
            criticalLabel.setText(String.valueOf(summary.criticalEvents()));
        } catch (RuntimeException ignored) {
            // The database may still be starting while the desktop window is already visible.
        }
    }

    private void appendLog(String message) {
        logArea.append("[%s] %s%n".formatted(LocalTime.now().format(TIME_FORMATTER), message));
        logArea.setCaretPosition(logArea.getDocument().getLength());
    }

    private double round(double value) {
        return Math.round(value * 100.0) / 100.0;
    }

    private record ProgressSnapshot(
            int savedEvents,
            int totalEvents,
            long elapsedMs,
            double eventsPerSecond,
            int progressPercent
    ) {
    }

    private record StreamSnapshot(
            long processedEvents,
            long failedEvents,
            long targetTotalEvents,
            long elapsedMs,
            long targetRatePerSecond,
            double eventsPerSecond,
            double successRatePercent,
            long backlog,
            int progressPercent
    ) {
    }
}
