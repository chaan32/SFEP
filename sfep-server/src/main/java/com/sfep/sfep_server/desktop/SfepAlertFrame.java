package com.sfep.sfep_server.desktop;

import com.sfep.sfep_server.alert.dto.AlertEventResponse;
import com.sfep.sfep_server.alert.service.AlertEventBus;
import com.sfep.sfep_server.event.domain.EventSeverity;

import javax.swing.BorderFactory;
import javax.swing.JFrame;
import javax.swing.JLabel;
import javax.swing.JPanel;
import javax.swing.JScrollPane;
import javax.swing.JTable;
import javax.swing.SwingUtilities;
import javax.swing.table.DefaultTableModel;
import java.awt.BorderLayout;
import java.awt.Color;
import java.awt.Dimension;
import java.awt.Font;
import java.awt.GridLayout;
import java.awt.Toolkit;
import java.awt.event.WindowAdapter;
import java.awt.event.WindowEvent;
import java.time.Duration;
import java.time.Instant;
import java.time.ZoneId;
import java.time.format.DateTimeFormatter;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.List;

public class SfepAlertFrame extends JFrame {

    private static final DateTimeFormatter TIME_FORMATTER = DateTimeFormatter.ofPattern("HH:mm:ss.SSS")
            .withZone(ZoneId.systemDefault());
    private static final int MAX_ROWS = 500;

    private final DefaultTableModel tableModel = new DefaultTableModel(
            new String[]{"발생 시각", "알림 발행", "UI 표시", "서버 지연(ms)", "UI 대기(ms)", "총 표시(ms)", "설비", "유형", "심각도", "온도", "진동", "전류", "메시지"},
            0
    ) {
        @Override
        public boolean isCellEditable(int row, int column) {
            return false;
        }
    };
    private final JLabel latestAlertLabel = new JLabel("아직 위험 이벤트가 없습니다.");
    private final JLabel totalAlertLabel = new JLabel("0");
    private final JLabel warningAlertLabel = new JLabel("0");
    private final JLabel criticalAlertLabel = new JLabel("0");
    private final JLabel averageServerLatencyLabel = new JLabel("0 ms");
    private final JLabel averageUiLatencyLabel = new JLabel("0 ms");
    private final JLabel averageTotalLatencyLabel = new JLabel("0 ms");
    private final JLabel p95TotalLatencyLabel = new JLabel("0 ms");
    private final JLabel p99TotalLatencyLabel = new JLabel("0 ms");
    private final Runnable unsubscribe;

    private long totalAlerts;
    private long warningAlerts;
    private long criticalAlerts;
    private long serverLatencyTotalMs;
    private long uiLatencyTotalMs;
    private long displayLatencyTotalMs;
    private final List<Long> displayLatencySamples = new ArrayList<>();

    public SfepAlertFrame(AlertEventBus alertEventBus) {
        super("SFEP Real-time Alert Monitor");
        setDefaultCloseOperation(JFrame.DISPOSE_ON_CLOSE);
        setMinimumSize(new Dimension(1120, 560));
        setLocationByPlatform(true);

        add(buildRootPanel(), BorderLayout.CENTER);
        unsubscribe = alertEventBus.subscribe(this::onAlert);
        addWindowListener(new WindowAdapter() {
            @Override
            public void windowClosed(WindowEvent event) {
                unsubscribe.run();
            }
        });
    }

    private JPanel buildRootPanel() {
        JPanel root = new JPanel(new BorderLayout(12, 12));
        root.setBorder(BorderFactory.createEmptyBorder(16, 16, 16, 16));
        root.add(buildSummaryPanel(), BorderLayout.NORTH);
        root.add(buildTablePanel(), BorderLayout.CENTER);
        return root;
    }

    private JPanel buildSummaryPanel() {
        JPanel panel = new JPanel(new BorderLayout(12, 12));
        panel.setBorder(BorderFactory.createTitledBorder("실시간 위험 알림"));

        latestAlertLabel.setOpaque(true);
        latestAlertLabel.setBackground(new Color(255, 245, 245));
        latestAlertLabel.setForeground(new Color(160, 32, 32));
        latestAlertLabel.setFont(latestAlertLabel.getFont().deriveFont(Font.BOLD, 16f));
        latestAlertLabel.setBorder(BorderFactory.createEmptyBorder(12, 12, 12, 12));

        panel.add(latestAlertLabel, BorderLayout.CENTER);
        panel.add(buildCounterPanel(), BorderLayout.EAST);
        return panel;
    }

    private JPanel buildCounterPanel() {
        JPanel panel = new JPanel(new GridLayout(2, 4, 10, 10));
        panel.add(counter("전체", totalAlertLabel));
        panel.add(counter("WARNING", warningAlertLabel));
        panel.add(counter("CRITICAL", criticalAlertLabel));
        panel.add(counter("평균 서버", averageServerLatencyLabel));
        panel.add(counter("평균 UI", averageUiLatencyLabel));
        panel.add(counter("평균 총 표시", averageTotalLatencyLabel));
        panel.add(counter("p95 총 표시", p95TotalLatencyLabel));
        panel.add(counter("p99 총 표시", p99TotalLatencyLabel));
        return panel;
    }

    private JPanel counter(String title, JLabel valueLabel) {
        JPanel panel = new JPanel(new BorderLayout(4, 4));
        JLabel titleLabel = new JLabel(title);
        titleLabel.setFont(titleLabel.getFont().deriveFont(Font.BOLD));
        valueLabel.setFont(valueLabel.getFont().deriveFont(Font.BOLD, 18f));
        panel.add(titleLabel, BorderLayout.NORTH);
        panel.add(valueLabel, BorderLayout.CENTER);
        panel.setBorder(BorderFactory.createEmptyBorder(8, 8, 8, 8));
        return panel;
    }

    private JScrollPane buildTablePanel() {
        JTable table = new JTable(tableModel);
        table.setAutoCreateRowSorter(true);
        table.setRowHeight(26);
        table.getColumnModel().getColumn(0).setPreferredWidth(110);
        table.getColumnModel().getColumn(1).setPreferredWidth(110);
        table.getColumnModel().getColumn(2).setPreferredWidth(110);
        table.getColumnModel().getColumn(3).setPreferredWidth(95);
        table.getColumnModel().getColumn(4).setPreferredWidth(85);
        table.getColumnModel().getColumn(5).setPreferredWidth(85);
        table.getColumnModel().getColumn(6).setPreferredWidth(90);
        table.getColumnModel().getColumn(7).setPreferredWidth(120);
        table.getColumnModel().getColumn(8).setPreferredWidth(90);
        table.getColumnModel().getColumn(12).setPreferredWidth(260);

        JScrollPane scrollPane = new JScrollPane(table);
        scrollPane.setBorder(BorderFactory.createTitledBorder("위험 이벤트 수신 내역"));
        return scrollPane;
    }

    private void onAlert(AlertEventResponse alert) {
        SwingUtilities.invokeLater(() -> appendAlert(alert));
    }

    private void appendAlert(AlertEventResponse alert) {
        Instant displayedAt = Instant.now();
        long uiQueueLatencyMs = Duration.between(alert.alertPublishedAt(), displayedAt).toMillis();
        long totalDisplayLatencyMs = Duration.between(alert.occurredAt(), displayedAt).toMillis();

        totalAlerts++;
        serverLatencyTotalMs += alert.alertLatencyMs();
        uiLatencyTotalMs += uiQueueLatencyMs;
        displayLatencyTotalMs += totalDisplayLatencyMs;
        displayLatencySamples.add(totalDisplayLatencyMs);
        if (displayLatencySamples.size() > MAX_ROWS) {
            displayLatencySamples.remove(0);
        }
        if (alert.severity() == EventSeverity.CRITICAL) {
            criticalAlerts++;
            Toolkit.getDefaultToolkit().beep();
        } else if (alert.severity() == EventSeverity.WARNING) {
            warningAlerts++;
        }

        latestAlertLabel.setText("%s | %s | %s | %s"
                .formatted(
                        TIME_FORMATTER.format(alert.occurredAt()),
                        alert.equipmentId(),
                        alert.equipmentType(),
                        "%s | 서버 %dms | UI 대기 %dms | 총 표시 %dms"
                                .formatted(alert.message(), alert.alertLatencyMs(), uiQueueLatencyMs, totalDisplayLatencyMs)
                ));
        totalAlertLabel.setText(String.valueOf(totalAlerts));
        warningAlertLabel.setText(String.valueOf(warningAlerts));
        criticalAlertLabel.setText(String.valueOf(criticalAlerts));
        averageServerLatencyLabel.setText(formatAverage(serverLatencyTotalMs));
        averageUiLatencyLabel.setText(formatAverage(uiLatencyTotalMs));
        averageTotalLatencyLabel.setText(formatAverage(displayLatencyTotalMs));
        p95TotalLatencyLabel.setText(formatPercentile(95));
        p99TotalLatencyLabel.setText(formatPercentile(99));

        tableModel.insertRow(0, new Object[]{
                TIME_FORMATTER.format(alert.occurredAt()),
                TIME_FORMATTER.format(alert.alertPublishedAt()),
                TIME_FORMATTER.format(displayedAt),
                alert.alertLatencyMs(),
                uiQueueLatencyMs,
                totalDisplayLatencyMs,
                alert.equipmentId(),
                alert.equipmentType(),
                alert.severity(),
                "%.2f".formatted(alert.temperature()),
                "%.2f".formatted(alert.vibration()),
                "%.2f".formatted(alert.currentValue()),
                alert.message()
        });

        while (tableModel.getRowCount() > MAX_ROWS) {
            tableModel.removeRow(tableModel.getRowCount() - 1);
        }
    }

    private String formatAverage(long totalLatencyMs) {
        if (totalAlerts == 0) {
            return "0 ms";
        }
        return "%.2f ms".formatted((double) totalLatencyMs / totalAlerts);
    }

    private String formatPercentile(int percentile) {
        if (displayLatencySamples.isEmpty()) {
            return "0 ms";
        }
        List<Long> sorted = displayLatencySamples.stream()
                .sorted(Comparator.naturalOrder())
                .toList();
        int index = (int) Math.ceil(percentile / 100.0 * sorted.size()) - 1;
        int boundedIndex = Math.max(0, Math.min(index, sorted.size() - 1));
        return "%d ms".formatted(sorted.get(boundedIndex));
    }
}
