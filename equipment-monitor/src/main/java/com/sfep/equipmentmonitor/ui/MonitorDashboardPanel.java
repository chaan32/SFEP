package com.sfep.equipmentmonitor.ui;

import com.sfep.equipmentmonitor.alert.AlertKind;
import com.sfep.equipmentmonitor.alert.HistoricalAlert;
import com.sfep.equipmentmonitor.replay.ReplayControllerState;
import com.sfep.equipmentmonitor.replay.ReplaySpeed;
import com.sfep.equipmentmonitor.replay.ReplayStatus;
import com.sfep.equipmentmonitor.replay.ReplayUnit;
import com.sfep.equipmentmonitor.risk.EvidenceFamily;
import com.sfep.equipmentmonitor.risk.MetricEvidence;
import com.sfep.equipmentmonitor.risk.RangeEvaluation;
import com.sfep.equipmentmonitor.risk.RangeSelectionReason;
import com.sfep.equipmentmonitor.risk.RangeStatus;
import com.sfep.equipmentmonitor.risk.ReasonCode;
import com.sfep.equipmentmonitor.risk.RiskGrade;
import com.sfep.equipmentmonitor.risk.RuleEvaluation;
import com.sfep.equipmentmonitor.risk.RuleMatchStatus;
import com.sfep.equipmentmonitor.state.EquipmentSnapshot;
import com.sfep.equipmentmonitor.state.EquipmentKey;
import com.sfep.equipmentmonitor.state.MaterialSnapshot;
import com.sfep.equipmentmonitor.state.MonitorUpdate;

import javax.swing.BorderFactory;
import javax.swing.Box;
import javax.swing.BoxLayout;
import javax.swing.JButton;
import javax.swing.JComboBox;
import javax.swing.JLabel;
import javax.swing.JPanel;
import javax.swing.JProgressBar;
import javax.swing.JScrollPane;
import javax.swing.JTabbedPane;
import javax.swing.JTable;
import javax.swing.RowSorter;
import javax.swing.SortOrder;
import javax.swing.SwingConstants;
import javax.swing.SwingUtilities;
import javax.swing.Timer;
import javax.swing.table.DefaultTableModel;
import javax.swing.table.TableRowSorter;
import java.awt.BorderLayout;
import java.awt.Color;
import java.awt.Dimension;
import java.awt.FlowLayout;
import java.awt.Font;
import java.awt.GridLayout;
import java.text.NumberFormat;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Objects;
import java.util.Set;
import java.util.Vector;

/** Single-screen desktop dashboard for sealed historical replay. */
public final class MonitorDashboardPanel extends JPanel {
    static final int MAX_RETAINED_EVIDENCE_ROWS = 2_000;

    @FunctionalInterface
    interface UpdateScheduler {
        void schedule(Runnable operation);
    }

    private static final String ALL = "전체";
    private static final String ASSOCIATION_NOTICE =
            "과거 품질 결과와 통계적으로 연결된 위험 근거이며, 직접 원인으로 단정하지 않음";
    private static final String MISSING_QUALITY_NOTICE =
            "입력 데이터가 없어 현재 소재의 품질 위험 연결 여부를 판정하지 않음";

    private final ReplayControl replayControl;
    private final UpdateScheduler updateScheduler;
    private final Object updateLock = new Object();
    private final JLabel replayStatus = new JLabel();
    private final JLabel replayFailureDetails = new JLabel(" ");
    private final JLabel replayDate = new JLabel("재생 날짜 · 시작 전");
    private final JLabel processedUnits = new JLabel("처리 · 0 시간대");
    private final JLabel replayProgressLabel = new JLabel();
    private final JProgressBar replayProgress = new JProgressBar(0, 100);
    private final JLabel unitNotice = new JLabel("시간대 데이터를 재생하면 처리 묶음이 표시됩니다.");
    private final long totalReplayEvents;
    private final ReadOnlyTableModel overviewModel = model(
            "설비 운전범위", "조기 품질 위험", "AP 후행 품질 확인",
            "소재", "공정 단계", "설비", "재생 시각", "우선순위");
    private final ReadOnlyTableModel equipmentModel = model(
            "설비 유형", "설비 ID", "공정 단계", "재생 시각", "시간대 소재 수", "소재별 대표 관측값");
    private final ReadOnlyTableModel equipmentMaterialModel = model(
            "설비", "소재", "공개 대표값", "설비 운전범위",
            "조기 품질 위험", "AP 후행 품질 확인", "시간대 내부 순서");
    private final ReadOnlyTableModel evidenceModel = model(
            "소재", "공정 단계", "구분", "항목/규칙", "판정", "관측값",
            "기준 수준", "발견구간 통계", "확인구간 통계", "해석");
    private final ReadOnlyTableModel historyModel = model(
            "상태", "재생 시각", "소재", "설비", "근거 유형", "필드", "관측값",
            "운전범위 판정", "반복", "규칙", "발견구간 통계", "확인구간 통계", "설명");
    private final ReadOnlyTableModel managementModel = model("구분", "항목", "봉인된 값");
    private final ReadOnlyTableModel definitionModel = model(
            "구분", "규칙 ID", "최초 공개 단계", "설비", "대상", "기준 수준", "정의/위험구간");
    private final Map<String, Integer> overviewRows = new LinkedHashMap<>();
    private final Map<String, MaterialSnapshot> latestMaterials = new LinkedHashMap<>();
    private final LinkedHashMap<String, EvidenceRow> retainedEvidenceRows = new LinkedHashMap<>();
    private final List<HistoricalAlert> allAlerts = new ArrayList<>();
    private final Set<String> historyDates = new LinkedHashSet<>();
    private final Set<String> historyEquipment = new LinkedHashSet<>();
    private final Set<String> historyMaterials = new LinkedHashSet<>();
    private final Set<String> historyGrades = new LinkedHashSet<>();
    private final JComboBox<String> historyDateFilter = new JComboBox<>(new String[]{ALL});
    private final JComboBox<String> historyEquipmentFilter = new JComboBox<>(new String[]{ALL});
    private final JComboBox<String> historyMaterialFilter = new JComboBox<>(new String[]{ALL});
    private final JComboBox<String> historyGradeFilter = new JComboBox<>(new String[]{ALL});
    private final JLabel severeSummary = summaryValue("summary-severe-value");
    private final JLabel cautionSummary = summaryValue("summary-caution-value");
    private final JLabel qualityDangerSummary = summaryValue("summary-quality-danger-value");
    private final JLabel materialSummary = summaryValue("summary-material-value");
    private final JLabel overviewSelectionDetail = new JLabel(
            "표에서 소재를 선택하면 판정 의미와 우선 확인 항목을 쉽게 설명합니다.");
    private final Timer stateRefreshTimer;
    private boolean updatingHistoryFilters;
    private PendingUpdates pendingUpdates;
    private boolean updateDrainScheduled;
    private boolean acceptingUpdates = true;
    private int replayedMaterialCountRow = -1;
    private JTable overviewTable;

    public MonitorDashboardPanel(MonitorDashboardMetadata metadata, ReplayControl replayControl) {
        this(metadata, replayControl, SwingUtilities::invokeLater);
    }

    MonitorDashboardPanel(
            MonitorDashboardMetadata metadata,
            ReplayControl replayControl,
            UpdateScheduler updateScheduler) {
        super(new BorderLayout(0, 14));
        this.replayControl = Objects.requireNonNull(replayControl, "replayControl");
        this.updateScheduler = Objects.requireNonNull(updateScheduler, "updateScheduler");
        Objects.requireNonNull(metadata, "metadata");
        totalReplayEvents = metadata.replayRows();
        updateReplayProgress(0, 0);
        setBorder(BorderFactory.createEmptyBorder(18, 20, 16, 20));
        setBackground(MonitorUiTheme.PAGE);

        add(header(), BorderLayout.NORTH);
        add(tabs(metadata), BorderLayout.CENTER);
        add(legend(), BorderLayout.SOUTH);

        refreshReplayState();
        stateRefreshTimer = new Timer(300, ignored -> refreshReplayState());
        stateRefreshTimer.setRepeats(true);
        stateRefreshTimer.start();
    }

    public void acceptUpdate(MonitorUpdate update) {
        Objects.requireNonNull(update, "update");
        synchronized (updateLock) {
            if (!acceptingUpdates) return;
            if (pendingUpdates == null) pendingUpdates = new PendingUpdates();
            pendingUpdates.merge(update);
            if (!updateDrainScheduled) {
                updateDrainScheduled = true;
                updateScheduler.schedule(this::drainUpdates);
            }
        }
    }

    public void refreshReplayState() {
        requireEdt();
        ReplayControllerState state;
        try {
            state = replayControl.state();
        } catch (RuntimeException error) {
            replayStatus.setText("! 데이터 오류 · " + message(error));
            return;
        }
        renderReplayState(state);
        String date = state.currentReplayDate();
        if (date != null && !date.isBlank()) {
            replayDate.setText("재생 날짜 · " + date);
        }
    }

    @Override
    public void removeNotify() {
        synchronized (updateLock) {
            acceptingUpdates = false;
            pendingUpdates = null;
            updateDrainScheduled = false;
        }
        stateRefreshTimer.stop();
        super.removeNotify();
    }

    private JPanel header() {
        JPanel header = new JPanel();
        header.setOpaque(false);
        header.setLayout(new BoxLayout(header, BoxLayout.Y_AXIS));

        JPanel title = MonitorUiTheme.brandCard(new BorderLayout(20, 0));
        title.setName("brand-header");
        title.setBorder(MonitorUiTheme.cardPadding(18, 22, 18, 22));
        JPanel titleCopy = new JPanel();
        titleCopy.setOpaque(false);
        titleCopy.setLayout(new BoxLayout(titleCopy, BoxLayout.Y_AXIS));
        titleCopy.add(MonitorUiTheme.label("SFEP 설비·품질 모니터", 23, Font.BOLD, Color.WHITE));
        titleCopy.add(Box.createVerticalStrut(5));
        titleCopy.add(MonitorUiTheme.label(
                "과거 데이터 재생 · HISTORICAL_REPLAY", 13, Font.BOLD,
                new Color(0xD7, 0xEB, 0xF5)));
        title.add(titleCopy, BorderLayout.WEST);
        replayStatus.setName("replay-status");
        replayStatus.setHorizontalAlignment(SwingConstants.RIGHT);
        replayStatus.setForeground(Color.WHITE);
        replayStatus.setFont(MonitorUiTheme.preferredFont(Font.BOLD, 14));
        title.add(replayStatus, BorderLayout.EAST);

        JPanel controlCard = MonitorUiTheme.card(new BorderLayout());
        controlCard.setBorder(MonitorUiTheme.cardPadding(12, 16, 12, 16));
        JPanel controls = new JPanel(new FlowLayout(FlowLayout.LEFT, 7, 5));
        controls.setOpaque(false);
        controls.add(button("시작", replayControl::start));
        controls.add(button("일시정지", replayControl::pause));
        controls.add(button("재개", replayControl::resume));
        controls.add(button("한 단계", replayControl::step));
        controls.add(button("정지", replayControl::stop));
        controls.add(Box.createHorizontalStrut(8));
        controls.add(MonitorUiTheme.label("재생 속도", 13, Font.BOLD, MonitorUiTheme.TEXT_MUTED));
        JComboBox<ReplaySpeed> speed = new JComboBox<>(ReplaySpeed.values());
        speed.setName("replay-speed");
        speed.setSelectedItem(ReplaySpeed.X1);
        speed.addActionListener(ignored -> runControl(
                () -> replayControl.setSpeed((ReplaySpeed) speed.getSelectedItem())));
        MonitorUiTheme.combo(speed);
        controls.add(speed);
        controls.add(Box.createHorizontalStrut(8));
        controls.add(button("다음 날짜", replayControl::advanceToNextDate));
        styleTimelineLabel(replayDate);
        controls.add(replayDate);
        controls.add(Box.createHorizontalStrut(16));
        styleTimelineLabel(processedUnits);
        controls.add(processedUnits);

        JPanel progressArea = new JPanel(new BorderLayout(0, 7));
        progressArea.setOpaque(false);
        progressArea.setBorder(BorderFactory.createEmptyBorder(7, 8, 5, 8));
        replayProgressLabel.setName("replay-progress-label");
        replayProgressLabel.setForeground(MonitorUiTheme.POSCO_BLUE);
        replayProgressLabel.setFont(MonitorUiTheme.preferredFont(Font.BOLD, 13));
        replayProgress.setName("replay-progress");
        replayProgress.getAccessibleContext().setAccessibleName("재생 진행률");
        MonitorUiTheme.progressBar(replayProgress);
        progressArea.add(replayProgressLabel, BorderLayout.NORTH);
        progressArea.add(replayProgress, BorderLayout.CENTER);

        JPanel context = new JPanel(new BorderLayout(12, 0));
        context.setOpaque(false);
        context.setBorder(BorderFactory.createEmptyBorder(2, 8, 0, 8));
        unitNotice.setForeground(MonitorUiTheme.TEXT_MUTED);
        unitNotice.setFont(MonitorUiTheme.preferredFont(Font.PLAIN, 13));
        replayFailureDetails.setName("replay-failure-details");
        replayFailureDetails.setForeground(MonitorUiTheme.DANGER);
        replayFailureDetails.setFont(MonitorUiTheme.preferredFont(Font.BOLD, 13));
        context.add(unitNotice, BorderLayout.WEST);
        context.add(replayFailureDetails, BorderLayout.EAST);
        JPanel progressAndContext = new JPanel();
        progressAndContext.setOpaque(false);
        progressAndContext.setLayout(new BoxLayout(progressAndContext, BoxLayout.Y_AXIS));
        progressAndContext.add(progressArea);
        progressAndContext.add(context);
        controlCard.add(controls, BorderLayout.CENTER);
        controlCard.add(progressAndContext, BorderLayout.SOUTH);
        header.add(title);
        header.add(Box.createVerticalStrut(12));
        header.add(controlCard);
        return header;
    }

    private JTabbedPane tabs(MonitorDashboardMetadata metadata) {
        JTabbedPane tabs = new JTabbedPane();
        tabs.setName("monitor-tabs");
        MonitorUiTheme.tabs(tabs);
        tabs.addTab("전체 현황", overviewTab());
        tabs.addTab("설비 상세", equipmentTab());
        tabs.addTab("위험 근거", evidenceTab());
        tabs.addTab("이력 조회", historyTab());
        tabs.addTab("관리 기준", managementTab(metadata));
        return tabs;
    }

    private JPanel overviewTab() {
        JPanel panel = new JPanel(new BorderLayout(0, 12));
        panel.setOpaque(false);
        panel.add(summaryCards(), BorderLayout.NORTH);
        panel.add(tablePanel(
                "확인이 필요한 소재가 위에 표시됩니다. 행을 선택하면 아래에서 의미를 설명합니다.",
                overviewModel,
                "overview-table"), BorderLayout.CENTER);

        JPanel explanation = MonitorUiTheme.card(new BorderLayout(12, 0));
        explanation.setBorder(MonitorUiTheme.cardPadding(14, 18, 14, 18));
        JLabel title = MonitorUiTheme.label("선택 항목 해석", 14, Font.BOLD, MonitorUiTheme.POSCO_BLUE);
        overviewSelectionDetail.setName("overview-selection-detail");
        overviewSelectionDetail.setForeground(MonitorUiTheme.TEXT);
        overviewSelectionDetail.setFont(MonitorUiTheme.preferredFont(Font.PLAIN, 13));
        explanation.add(title, BorderLayout.WEST);
        explanation.add(overviewSelectionDetail, BorderLayout.CENTER);
        panel.add(explanation, BorderLayout.SOUTH);
        return panel;
    }

    private JPanel summaryCards() {
        JPanel cards = new JPanel(new GridLayout(1, 4, 12, 0));
        cards.setName("priority-risk-panel");
        cards.setOpaque(false);
        cards.add(summaryCard(
                "■ 심한 설비 편차", severeSummary,
                "즉시 설비 상태 확인", MonitorUiTheme.DANGER, MonitorUiTheme.DANGER_TINT));
        cards.add(summaryCard(
                "▲ 주의 설비 편차", cautionSummary,
                "변화 추이를 계속 관찰", MonitorUiTheme.CAUTION, MonitorUiTheme.CAUTION_TINT));
        cards.add(summaryCard(
                "■ 조기 품질 위험", qualityDangerSummary,
                "과거 품질 결과와 연결", MonitorUiTheme.DANGER, MonitorUiTheme.DANGER_TINT));
        cards.add(summaryCard(
                "재생된 소재", materialSummary,
                "현재까지 공개된 고유 소재", MonitorUiTheme.POSCO_BLUE,
                new Color(0xE8, 0xF4, 0xFA)));
        return cards;
    }

    private static JPanel summaryCard(
            String title,
            JLabel value,
            String caption,
            Color accent,
            Color tint) {
        JPanel card = MonitorUiTheme.card(new BorderLayout(8, 0));
        card.setBorder(MonitorUiTheme.cardPadding(14, 16, 14, 16));
        JPanel marker = new JPanel();
        marker.setBackground(accent);
        marker.setPreferredSize(new Dimension(5, 1));
        card.add(marker, BorderLayout.WEST);
        JPanel copy = new JPanel();
        copy.setOpaque(false);
        copy.setLayout(new BoxLayout(copy, BoxLayout.Y_AXIS));
        JLabel heading = MonitorUiTheme.label(title, 13, Font.BOLD, accent);
        heading.setOpaque(true);
        heading.setBackground(tint);
        heading.setBorder(BorderFactory.createEmptyBorder(3, 7, 3, 7));
        copy.add(heading);
        copy.add(Box.createVerticalStrut(8));
        copy.add(value);
        copy.add(Box.createVerticalStrut(3));
        copy.add(MonitorUiTheme.label(caption, 12, Font.PLAIN, MonitorUiTheme.TEXT_MUTED));
        card.add(copy, BorderLayout.CENTER);
        return card;
    }

    private JPanel evidenceTab() {
        JPanel panel = tablePanel(
                "현재 처리 묶음과 최근 근거 최대 " + number(MAX_RETAINED_EVIDENCE_ROWS)
                        + "건 · 전체 경보 통계는 이력 조회에서 확인",
                evidenceModel,
                "evidence-table");
        JLabel note = new JLabel(
                "설비 관측 범위와 품질 위험 연결 근거는 서로 다른 축이며 직접 원인을 뜻하지 않습니다.");
        note.setBorder(BorderFactory.createEmptyBorder(8, 4, 2, 4));
        note.setForeground(MonitorUiTheme.TEXT_MUTED);
        note.setFont(MonitorUiTheme.preferredFont(Font.PLAIN, 12));
        panel.add(note, BorderLayout.SOUTH);
        return panel;
    }

    private JPanel equipmentTab() {
        JPanel panel = new JPanel(new GridLayout(2, 1, 0, 12));
        panel.setOpaque(false);
        panel.add(tablePanel(
                "현재 시간대 설비 요약이며 시간대 내부 소재 순서를 뜻하지 않습니다.",
                equipmentModel, "equipment-table"));
        panel.add(tablePanel(
                "현재 시간대의 모든 소재별 공개 대표값과 세 판정 축입니다.",
                equipmentMaterialModel, "equipment-material-table"));
        return panel;
    }

    private JPanel historyTab() {
        JPanel panel = tablePanel(ASSOCIATION_NOTICE, historyModel, "history-table");
        JPanel header = new JPanel();
        header.setOpaque(false);
        header.setLayout(new BoxLayout(header, BoxLayout.Y_AXIS));
        JLabel notice = MonitorUiTheme.label(
                ASSOCIATION_NOTICE, 12, Font.PLAIN, MonitorUiTheme.TEXT_MUTED);
        header.add(notice);
        JPanel filters = new JPanel(new FlowLayout(FlowLayout.LEFT, 7, 3));
        filters.setOpaque(false);
        configureFilter(historyDateFilter, "history-date-filter", "날짜", filters);
        configureFilter(historyEquipmentFilter, "history-equipment-filter", "설비", filters);
        configureFilter(historyMaterialFilter, "history-material-filter", "소재", filters);
        configureFilter(historyGradeFilter, "history-grade-filter", "등급", filters);
        header.add(filters);
        panel.add(header, BorderLayout.NORTH);
        return panel;
    }

    private void configureFilter(
            JComboBox<String> filter,
            String name,
            String label,
            JPanel parent) {
        filter.setName(name);
        MonitorUiTheme.combo(filter);
        filter.addActionListener(ignored -> {
            if (!updatingHistoryFilters) rebuildHistory();
        });
        parent.add(MonitorUiTheme.label(label, 13, Font.BOLD, MonitorUiTheme.TEXT_MUTED));
        parent.add(filter);
    }

    private JPanel managementTab(MonitorDashboardMetadata metadata) {
        managementModel.addRow(new Object[]{"식별자", "bundle ID", metadata.bundleId()});
        managementModel.addRow(new Object[]{"식별자", "criteria ID", metadata.criteriaId()});
        managementModel.addRow(new Object[]{"봉인 기준", "기준 생성 시각", metadata.asOf()});
        managementModel.addRow(new Object[]{"봉인 기준", "평가 모드", metadata.evaluationMode()});
        managementModel.addRow(new Object[]{
                "봉인 기준", "분석 기간", metadata.dateFrom() + " ~ " + metadata.dateTo()});
        managementModel.addRow(new Object[]{
                "평가 표본", "독립 평가(holdout) 대상", number(metadata.holdoutTotal())});
        replayedMaterialCountRow = managementModel.getRowCount();
        managementModel.addRow(new Object[]{
                "재생 진행", "현재까지 재생된 고유 소재 수 (완료 시 전체)", "0"});
        managementModel.addRow(new Object[]{
                "재생 규모", "재생 이벤트 건수", number(metadata.replayRows())});
        managementModel.addRow(new Object[]{
                "정의 수", "설비 운전범위 정의 수", number(metadata.rangeDefinitionCount())});
        managementModel.addRow(new Object[]{
                "정의 수", "품질 위험 연결 정의 수", number(metadata.qualityRuleDefinitionCount())});
        metadata.managementFacts().forEach(fact -> managementModel.addRow(new Object[]{
                fact.category(), fact.name(), fact.value()}));
        definitionModel.addRows(metadata.definitions().stream()
                .map(definition -> new Object[]{
                        definition.kind(),
                        definition.ruleId(),
                        stageLabel(definition.stage()),
                        definitionEquipment(definition.equipment()),
                        definition.target(),
                        definition.level(),
                        definition.interval()
                })
                .toList());

        JPanel panel = new JPanel(new GridLayout(2, 1, 0, 12));
        panel.setOpaque(false);
        panel.add(tablePanel(
                "화면의 식별자·해시·감사 집계는 로드 시 검증된 봉인 번들의 값입니다.",
                managementModel, "management-table"));
        panel.add(tablePanel(
                "봉인된 설비 운전범위와 역사적 품질 위험 연결 정의입니다.",
                definitionModel, "management-definitions-table"));
        return panel;
    }

    private JPanel tablePanel(String description, DefaultTableModel model, String name) {
        JPanel panel = MonitorUiTheme.card(new BorderLayout(0, 10));
        panel.setBorder(MonitorUiTheme.cardPadding(14, 16, 16, 16));
        JLabel label = MonitorUiTheme.label(description, 13, Font.PLAIN, MonitorUiTheme.TEXT_MUTED);
        panel.add(label, BorderLayout.NORTH);
        JTable table = new JTable(model);
        table.setName(name);
        table.setAutoCreateRowSorter(!"overview-table".equals(name));
        table.setFillsViewportHeight(true);
        table.setAutoResizeMode(JTable.AUTO_RESIZE_OFF);
        table.getTableHeader().setReorderingAllowed(false);
        MonitorUiTheme.table(table);
        configureColumnWidths(table);
        if ("overview-table".equals(name)) configureOverviewTable(table);
        JScrollPane scroll = new JScrollPane(table);
        MonitorUiTheme.scrollPane(scroll);
        scroll.setPreferredSize(new Dimension(1100, 500));
        panel.add(scroll, BorderLayout.CENTER);
        return panel;
    }

    private JButton button(String text, Runnable operation) {
        JButton button = new JButton(text);
        switch (text) {
            case "시작" -> MonitorUiTheme.primaryButton(button);
            case "일시정지", "재개", "다음 날짜" -> MonitorUiTheme.secondaryButton(button);
            default -> MonitorUiTheme.quietButton(button);
        }
        button.addActionListener(ignored -> runControl(operation));
        return button;
    }

    private JPanel legend() {
        JPanel legend = MonitorUiTheme.card(new FlowLayout(FlowLayout.LEFT, 14, 7));
        legend.setBorder(MonitorUiTheme.cardPadding(5, 12, 5, 12));
        legend.add(MonitorUiTheme.label("판정 안내", 12, Font.BOLD, MonitorUiTheme.POSCO_BLUE));
        legend.add(legendItem("운전범위 · ● 전형 범위", MonitorUiTheme.SUCCESS));
        legend.add(legendItem("▲ 주의 편차", MonitorUiTheme.CAUTION));
        legend.add(legendItem("■ 심한 편차", MonitorUiTheme.DANGER));
        legend.add(legendItem("품질 · ● 정상", MonitorUiTheme.SUCCESS));
        legend.add(legendItem("▲ 주의", MonitorUiTheme.CAUTION));
        legend.add(legendItem("■ 위험", MonitorUiTheme.DANGER));
        legend.add(legendItem("? 근거 부족", MonitorUiTheme.TEXT_MUTED));
        legend.add(legendItem("! 데이터 오류", MonitorUiTheme.DANGER));
        return legend;
    }

    private static JLabel legendItem(String text, Color colour) {
        JLabel label = MonitorUiTheme.label(text, 12, Font.BOLD, colour);
        label.setBorder(BorderFactory.createEmptyBorder(2, 4, 2, 4));
        return label;
    }

    private static JLabel summaryValue(String name) {
        JLabel value = MonitorUiTheme.label("0", 27, Font.BOLD, MonitorUiTheme.TEXT);
        value.setName(name);
        return value;
    }

    private static void styleTimelineLabel(JLabel label) {
        label.setForeground(MonitorUiTheme.TEXT);
        label.setFont(MonitorUiTheme.preferredFont(Font.BOLD, 13));
        label.setBorder(BorderFactory.createCompoundBorder(
                BorderFactory.createLineBorder(MonitorUiTheme.LINE, 1, true),
                BorderFactory.createEmptyBorder(7, 10, 7, 10)));
    }

    private void configureOverviewTable(JTable table) {
        overviewTable = table;
        int priorityModelColumn = overviewModel.findColumn("우선순위");
        TableRowSorter<DefaultTableModel> sorter = new TableRowSorter<>(overviewModel);
        sorter.setComparator(priorityModelColumn,
                Comparator.comparingInt(value -> ((Number) value).intValue()));
        sorter.setSortKeys(List.of(
                new RowSorter.SortKey(priorityModelColumn, SortOrder.ASCENDING)));
        sorter.setSortsOnUpdates(true);
        table.setRowSorter(sorter);
        table.removeColumn(table.getColumn("우선순위"));
        table.setSelectionMode(javax.swing.ListSelectionModel.SINGLE_SELECTION);
        table.getSelectionModel().addListSelectionListener(event -> {
            if (!event.getValueIsAdjusting()) renderSelectedMaterial(table);
        });
    }

    private static void configureColumnWidths(JTable table) {
        for (int column = 0; column < table.getColumnCount(); column++) {
            String name = table.getColumnName(column);
            int width = switch (name) {
                case "상태", "판정", "운전범위 판정", "설비 운전범위",
                        "조기 품질 위험", "AP 후행 품질 확인" -> 145;
                case "소재", "규칙 ID", "필드", "항목/규칙", "대상" -> 170;
                case "설비", "설비 유형", "설비 ID", "공정 단계", "최초 공개 단계" -> 175;
                case "재생 시각", "기준 수준", "시간대 내부 순서" -> 160;
                case "공개 대표값", "소재별 대표 관측값", "정의/위험구간" -> 280;
                case "발견구간 통계", "확인구간 통계", "해석", "설명", "봉인된 값" -> 360;
                default -> 130;
            };
            table.getColumnModel().getColumn(column).setPreferredWidth(width);
        }
    }

    private void runControl(Runnable operation) {
        try {
            operation.run();
            refreshReplayState();
        } catch (RuntimeException error) {
            replayStatus.setText("! 데이터 오류 · " + message(error));
        }
    }

    private void drainUpdates() {
        requireEdt();
        PendingUpdates batch;
        synchronized (updateLock) {
            if (!acceptingUpdates) {
                pendingUpdates = null;
                updateDrainScheduled = false;
                return;
            }
            batch = pendingUpdates;
            pendingUpdates = null;
        }
        try {
            if (batch != null) applyUpdates(batch);
        } catch (RuntimeException error) {
            synchronized (updateLock) {
                acceptingUpdates = false;
                pendingUpdates = null;
            }
            if (batch != null && batch.lastUnit != null) {
                try {
                    replayControl.reportUiFailure(batch.lastUnit, error);
                    renderReplayState(replayControl.state());
                } catch (RuntimeException reportingFailure) {
                    replayStatus.setText("! 데이터 오류 · " + message(error));
                    replayFailureDetails.setText("오류 보고 실패 · " + message(reportingFailure));
                }
            } else {
                replayStatus.setText("! 데이터 오류 · " + message(error));
            }
        } finally {
            synchronized (updateLock) {
                if (!acceptingUpdates) {
                    pendingUpdates = null;
                    updateDrainScheduled = false;
                } else if (pendingUpdates == null) {
                    updateDrainScheduled = false;
                } else {
                    updateScheduler.schedule(this::drainUpdates);
                }
            }
        }
    }

    private void applyUpdates(PendingUpdates update) {
        requireEdt();
        ReplayControllerState state = replayControl.state();
        String date = update.lastUnit.replayDate();
        replayDate.setText("재생 날짜 · " + date);
        updateReplayProgress(update.unitsProcessed, update.eventsProcessed);
        unitNotice.setText(unitNotice(update.lastUnit));

        upsertMaterials(update.changedMaterials.values());
        refreshSummaryCards();
        replaceCurrentEquipmentDetails(update.lastChangedMaterials, update.lastChangedEquipment);
        retainEvidence(update.evidenceRows.values());
        retainAlertEvidence(update.alerts);
        renderRetainedEvidence();
        appendAlerts(update.alerts);
        renderReplayState(state);
    }

    private void updateReplayProgress(long unitsProcessed, long eventsProcessed) {
        long safeUnits = Math.max(0, unitsProcessed);
        long safeEvents = Math.max(0, eventsProcessed);
        long displayedEvents = Math.min(safeEvents, totalReplayEvents);
        int percentage = replayPercentage(displayedEvents, totalReplayEvents);
        processedUnits.setText("처리 · " + number(safeUnits) + " 시간대");
        String text = "재생 진행 · " + number(displayedEvents)
                + " / " + number(totalReplayEvents) + " 이벤트 · " + percentage + "%";
        replayProgressLabel.setText(text);
        replayProgress.setValue(percentage);
        replayProgress.getAccessibleContext().setAccessibleDescription(text);
    }

    private static int replayPercentage(long processed, long total) {
        if (total <= 0 || processed <= 0) return 0;
        if (processed >= total) return 100;
        return (int) Math.min(99, Math.round(processed * 100.0 / total));
    }

    private void upsertMaterials(Iterable<MaterialSnapshot> materials) {
        String selectedMaterial = selectedOverviewMaterial();
        Map<Integer, Object[]> replacements = new LinkedHashMap<>();
        List<Object[]> additions = new ArrayList<>();
        for (MaterialSnapshot material : materials) {
            Object[] row = {
                    operatingRangeSymbol(material.rangeEvaluations()),
                    qualityStatus(material, false),
                    qualityStatus(material, true),
                    material.materialKey(),
                    stageLabel(material.eventStage()),
                    equipmentLabel(material.equipmentType(), material.equipmentId()),
                    replayTime(material.replayDate(), material.replayHour()),
                    actionPriority(material)
            };
            Integer modelRow = overviewRows.get(material.materialKey());
            if (modelRow == null) {
                overviewRows.put(material.materialKey(), overviewRows.size());
                additions.add(row);
            } else {
                replacements.put(modelRow, row);
            }
            latestMaterials.put(material.materialKey(), material);
        }
        overviewModel.applyRows(replacements, additions);
        restoreOverviewSelection(selectedMaterial);
        if (replayedMaterialCountRow >= 0) {
            managementModel.setValueAt(
                    number(overviewRows.size()), replayedMaterialCountRow, 2);
        }
    }

    private String selectedOverviewMaterial() {
        if (overviewTable == null || overviewTable.getSelectedRow() < 0) return null;
        int modelRow = overviewTable.convertRowIndexToModel(overviewTable.getSelectedRow());
        return String.valueOf(overviewModel.getValueAt(modelRow, 3));
    }

    private void restoreOverviewSelection(String materialKey) {
        if (materialKey == null || overviewTable == null) return;
        Integer modelRow = overviewRows.get(materialKey);
        if (modelRow == null) return;
        int viewRow = overviewTable.convertRowIndexToView(modelRow);
        if (viewRow >= 0) overviewTable.setRowSelectionInterval(viewRow, viewRow);
    }

    private void refreshSummaryCards() {
        long severe = latestMaterials.values().stream()
                .filter(material -> material.rangeEvaluations().stream()
                        .anyMatch(value -> value.status() == RangeStatus.SEVERE))
                .count();
        long caution = latestMaterials.values().stream()
                .filter(material -> material.rangeEvaluations().stream()
                        .noneMatch(value -> value.status() == RangeStatus.SEVERE))
                .filter(material -> material.rangeEvaluations().stream()
                        .anyMatch(value -> value.status() == RangeStatus.CAUTION))
                .count();
        long qualityDanger = latestMaterials.values().stream()
                .filter(material -> material.qualityRisk() == RiskGrade.DANGER)
                .count();
        severeSummary.setText(number(severe));
        cautionSummary.setText(number(caution));
        qualityDangerSummary.setText(number(qualityDanger));
        materialSummary.setText(number(latestMaterials.size()));
    }

    private void renderSelectedMaterial(JTable table) {
        int viewRow = table.getSelectedRow();
        if (viewRow < 0) {
            overviewSelectionDetail.setText(
                    "표에서 소재를 선택하면 판정 의미와 우선 확인 항목을 쉽게 설명합니다.");
            return;
        }
        int modelRow = table.convertRowIndexToModel(viewRow);
        String range = String.valueOf(overviewModel.getValueAt(modelRow, 0));
        String quality = String.valueOf(overviewModel.getValueAt(modelRow, 1));
        String ap = String.valueOf(overviewModel.getValueAt(modelRow, 2));
        String material = String.valueOf(overviewModel.getValueAt(modelRow, 3));
        int priority = ((Number) overviewModel.getValueAt(modelRow, 7)).intValue();
        String action = switch (priority) {
            case 0 -> "즉시 확인 필요";
            case 1 -> "주의 관찰 필요";
            case 2 -> "판정 근거 확인 필요";
            default -> "현재 우선 확인 대상 아님";
        };
        overviewSelectionDetail.setText("<html><b>" + html(material) + " · " + action + "</b>"
                + " &nbsp; 설비 운전범위: " + html(plainStatus(range))
                + " &nbsp; 조기 품질 위험: " + html(plainStatus(quality))
                + " &nbsp; AP 후행 품질: " + html(plainStatus(ap)) + "</html>");
    }

    private void replaceCurrentEquipmentDetails(
            Map<String, MaterialSnapshot> materials,
            Map<EquipmentKey, EquipmentSnapshot> equipment) {
        List<EquipmentSnapshot> currentEquipment = equipment.values().stream()
                .sorted(Comparator.comparing(
                        value -> value.key().equipmentType() + value.key().equipmentId()))
                .toList();
        equipmentModel.replaceRows(currentEquipment.stream()
                .map(snapshot -> new Object[]{
                        equipmentTypeLabel(snapshot.key().equipmentType()),
                        snapshot.key().equipmentId(),
                        stageLabel(snapshot.eventStage()),
                        replayTime(snapshot.replayDate(), snapshot.replayHour()),
                        snapshot.materialKeys().size(),
                        summarizeValues(snapshot)
                })
                .toList());
        List<Object[]> rows = new ArrayList<>();
        currentEquipment.forEach(snapshot -> snapshot.materialKeys().forEach(materialKey -> {
                    MaterialSnapshot material = materials.get(materialKey);
                    rows.add(new Object[]{
                            equipmentLabel(snapshot.key().equipmentType(), snapshot.key().equipmentId()),
                            materialKey,
                            representativeValues(snapshot.valuesByMaterial().get(materialKey)),
                            material == null ? "? 근거 부족" : operatingRangeSymbol(material.rangeEvaluations()),
                            material == null ? "? 근거 부족" : qualityStatus(material, false),
                            material == null ? "? 근거 부족" : qualityStatus(material, true),
                            snapshot.orderedWithinUnit() ? "입력 순서 제공" : "정확한 순서 없음"
                    });
                }));
        equipmentMaterialModel.replaceRows(rows);
    }

    private void retainEvidence(Iterable<EvidenceRow> rows) {
        for (EvidenceRow row : rows) {
            retainEvidence(row);
        }
    }

    private void retainAlertEvidence(List<HistoricalAlert> alerts) {
        int first = Math.max(0, alerts.size() - MAX_RETAINED_EVIDENCE_ROWS);
        for (int index = first; index < alerts.size(); index++) {
            retainEvidence(alertEvidenceRow(alerts.get(index)));
        }
    }

    private void retainEvidence(EvidenceRow row) {
        retainedEvidenceRows.remove(row.key());
        retainedEvidenceRows.put(row.key(), row);
        while (retainedEvidenceRows.size() > MAX_RETAINED_EVIDENCE_ROWS) {
            String eldest = retainedEvidenceRows.keySet().iterator().next();
            retainedEvidenceRows.remove(eldest);
        }
    }

    private void renderRetainedEvidence() {
        evidenceModel.replaceRows(retainedEvidenceRows.values().stream()
                .map(row -> row.values().toArray())
                .toList());
    }

    private void appendAlerts(List<HistoricalAlert> alerts) {
        if (alerts.isEmpty()) return;
        allAlerts.addAll(alerts);
        addHistoryFilterOptions(alerts);
        List<Object[]> visibleRows = alerts.stream()
                .filter(this::matchesHistoryFilters)
                .map(this::historyRow)
                .toList();
        historyModel.addRows(visibleRows);
    }

    private void rebuildHistory() {
        requireEdt();
        historyModel.replaceRows(allAlerts.stream()
                .filter(this::matchesHistoryFilters)
                .map(this::historyRow)
                .toList());
    }

    private Object[] historyRow(HistoricalAlert alert) {
        return new Object[]{
                alertStatus(alert),
                replayTime(alert.replayDate(), alert.replayHour()),
                alert.materialKey(),
                equipmentLabel(alert.equipmentType(), alert.equipmentId()),
                alert.kind() == AlertKind.QUALITY_RISK ? "품질 위험 연결" : "설비 운전범위",
                value(alert.field()),
                value(alert.observedValue()),
                alert.kind() == AlertKind.OPERATING_RANGE
                        ? rangeStatusLabel(alert.rangeStatus()) : "-",
                alert.repeated() ? "반복 확인 필요" : "반복 아님",
                shortId(alert.ruleId()),
                alert.kind() == AlertKind.QUALITY_RISK
                        ? metricText("발견구간", alert.discovery()) : "-",
                alert.kind() == AlertKind.QUALITY_RISK
                        ? metricText("확인구간 결과", alert.confirmation()) : "-",
                alert.kind() == AlertKind.QUALITY_RISK
                        ? ASSOCIATION_NOTICE
                        : "과거 설비 관측 분포 범위를 벗어난 이력이며 품질의 직접 원인으로 단정하지 않음"
        };
    }

    private static EvidenceRow alertEvidenceRow(HistoricalAlert alert) {
        String key = "A|" + alert.kind() + "|" + alert.materialKey() + "|"
                + alert.eventStage() + "|" + alert.ruleId();
        if (alert.kind() == AlertKind.OPERATING_RANGE) {
            return new EvidenceRow(key, List.of(
                    alert.materialKey(),
                    stageLabel(alert.eventStage()),
                    "설비 운전범위 경보",
                    value(alert.field()),
                    rangeStatusLabel(alert.rangeStatus()),
                    value(alert.observedValue()),
                    "규칙 " + shortId(alert.ruleId())
                            + (alert.repeated() ? " · 반복 확인 필요" : ""),
                    "-",
                    "-",
                    "과거 설비 관측 분포 범위를 벗어난 이력이며 품질의 직접 원인으로 단정하지 않음"));
        }
        return new EvidenceRow(key, List.of(
                alert.materialKey(),
                stageLabel(alert.eventStage()),
                "품질 위험 경보",
                value(alert.field()),
                symbol(alert.grade()),
                value(alert.observedValue()),
                "규칙 " + shortId(alert.ruleId())
                        + (alert.repeated() ? " · 반복 확인 필요" : ""),
                metricText("발견구간", alert.discovery()),
                metricText("확인구간 결과", alert.confirmation()),
                ASSOCIATION_NOTICE));
    }

    private static String unitNotice(ReplayUnit unit) {
        int materials = unit.events().size();
        if (!unit.orderedWithinUnit()) {
            return "시간대 처리 소재 " + number(materials) + "건, 정확한 처리순서 없음";
        }
        return "시간대 처리 소재 " + number(materials) + "건";
    }

    private static String summarizeValues(EquipmentSnapshot equipment) {
        List<String> values = new ArrayList<>();
        equipment.valuesByMaterial().entrySet().stream()
                .sorted(Map.Entry.comparingByKey())
                .forEach(entry -> values.add(entry.getKey() + "=" + entry.getValue()));
        return String.join("; ", values);
    }

    private static String representativeValues(Map<String, com.sfep.equipmentmonitor.risk.RiskScalar> values) {
        if (values == null || values.isEmpty()) return "-";
        return values.entrySet().stream()
                .sorted(Map.Entry.comparingByKey())
                .map(entry -> entry.getKey() + "=" + entry.getValue())
                .collect(java.util.stream.Collectors.joining(", "));
    }

    private void addHistoryFilterOptions(List<HistoricalAlert> alerts) {
        List<String> dates = newValues(alerts.stream().map(HistoricalAlert::replayDate).toList(), historyDates);
        List<String> equipment = newValues(alerts.stream()
                .map(alert -> equipmentLabel(alert.equipmentType(), alert.equipmentId())).toList(), historyEquipment);
        List<String> materials = newValues(
                alerts.stream().map(HistoricalAlert::materialKey).toList(), historyMaterials);
        List<String> grades = newValues(
                alerts.stream().map(MonitorDashboardPanel::alertGradeFilterLabel).toList(), historyGrades);
        updatingHistoryFilters = true;
        try {
            dates.forEach(historyDateFilter::addItem);
            equipment.forEach(historyEquipmentFilter::addItem);
            materials.forEach(historyMaterialFilter::addItem);
            grades.stream()
                    .sorted(Comparator.comparingInt(MonitorDashboardPanel::gradeFilterOrder))
                    .forEach(historyGradeFilter::addItem);
        } finally {
            updatingHistoryFilters = false;
        }
    }

    private boolean matchesHistoryFilters(HistoricalAlert alert) {
        return selectedMatches(historyDateFilter, alert.replayDate())
                && selectedMatches(
                        historyEquipmentFilter,
                        equipmentLabel(alert.equipmentType(), alert.equipmentId()))
                && selectedMatches(historyMaterialFilter, alert.materialKey())
                && selectedMatches(historyGradeFilter, alertGradeFilterLabel(alert));
    }

    private static List<String> newValues(List<String> candidates, Set<String> known) {
        List<String> additions = candidates.stream()
                .filter(known::add)
                .distinct()
                .sorted()
                .toList();
        return additions;
    }

    private static boolean selectedMatches(JComboBox<String> filter, String value) {
        String selected = selected(filter);
        return ALL.equals(selected) || Objects.equals(selected, value);
    }

    private static String selected(JComboBox<String> filter) {
        Object selected = filter.getSelectedItem();
        return selected == null ? ALL : selected.toString();
    }

    private static String operatingRangeSymbol(List<RangeEvaluation> evaluations) {
        if (evaluations.stream().anyMatch(value -> value.status() == RangeStatus.SEVERE)) {
            return "■ 심한 편차";
        }
        if (evaluations.stream().anyMatch(value -> value.status() == RangeStatus.CAUTION)) {
            return "▲ 주의 편차";
        }
        if (!evaluations.isEmpty()
                && evaluations.stream().allMatch(value -> value.status() == RangeStatus.TYPICAL)) {
            return "● 전형 범위";
        }
        return "? 근거 부족";
    }

    private static int actionPriority(MaterialSnapshot material) {
        boolean severe = material.rangeEvaluations().stream()
                .anyMatch(value -> value.status() == RangeStatus.SEVERE);
        boolean caution = material.rangeEvaluations().stream()
                .anyMatch(value -> value.status() == RangeStatus.CAUTION);
        if (severe || material.qualityRisk() == RiskGrade.DANGER
                || material.historicalEvidenceRisk() == RiskGrade.DANGER) return 0;
        if (caution || material.qualityRisk() == RiskGrade.CAUTION
                || material.historicalEvidenceRisk() == RiskGrade.CAUTION) return 1;
        boolean insufficient = material.rangeEvaluations().isEmpty()
                || material.rangeEvaluations().stream().anyMatch(value -> switch (value.status()) {
                    case REFERENCE_ONLY, DATA_MISSING, UNREGISTERED_CONDITION, INSUFFICIENT_EVIDENCE -> true;
                    default -> false;
                })
                || material.qualityRisk() == RiskGrade.UNCONFIRMED
                || material.qualityRisk() == RiskGrade.INSUFFICIENT_EVIDENCE
                || material.historicalEvidenceRisk() == RiskGrade.UNCONFIRMED
                || material.historicalEvidenceRisk() == RiskGrade.INSUFFICIENT_EVIDENCE
                || material.ruleEvaluations().stream()
                        .anyMatch(value -> value.status() == RuleMatchStatus.DATA_MISSING);
        return insufficient ? 2 : 3;
    }

    private static String plainStatus(String status) {
        if (status == null || status.length() < 3) return value(status);
        if (status.startsWith("● ") || status.startsWith("▲ ")
                || status.startsWith("■ ") || status.startsWith("? ")
                || status.startsWith("! ")) {
            return status.substring(2);
        }
        return status;
    }

    private static String html(String value) {
        return value.replace("&", "&amp;")
                .replace("<", "&lt;")
                .replace(">", "&gt;")
                .replace("\"", "&quot;");
    }

    private static String rangeCriteria(RangeEvaluation range) {
        String level = range.contextLevel() == null ? "맥락 수준 없음" : "맥락 수준 " + range.contextLevel();
        return level + " · " + selectionReasonLabel(range.selectionReason());
    }

    private static String ruleCriteria(RuleEvaluation rule) {
        String applicability;
        if (rule.status() == RuleMatchStatus.DATA_MISSING) {
            applicability = rule.historicalEvidenceOnly()
                    ? "AP 후행 품질 확인 판정 보류"
                    : "조기 품질 위험 판정 보류";
        } else if (rule.historicalEvidenceOnly()) {
            applicability = "AP 후행 품질 확인 전용";
        } else if (rule.alertEligible()) {
            applicability = "조기 품질 위험 대상";
        } else {
            applicability = "조기 품질 위험 비대상";
        }
        return evidenceFamilyLabel(rule.evidenceFamily()) + " 근거군 · "
                + ruleStatusLabel(rule.status()) + " · " + applicability;
    }

    private static String metricText(String section, MetricEvidence evidence) {
        if (evidence == null) return section + " · 통계 근거 없음";
        return section
                + " · 표본 " + number(evidence.support()) + " / 불량 " + number(evidence.defects())
                + " · 보정 불량률 " + percent(evidence.adjustedRate())
                + " · 관측 불량률 " + percent(evidence.crudeRate())
                + " · Wilson 95% CI " + percentageInterval(
                        evidence.crudeRateCiLower(), evidence.crudeRateCiUpper())
                + " · 비교군 보정 불량률 " + percent(evidence.comparatorAdjustedRate())
                + " · " + relativeRisk(evidence)
                + " · q-value " + decimal(evidence.qValue(), 4);
    }

    private static String relativeRisk(MetricEvidence evidence) {
        if (evidence.reasonCode() != ReasonCode.NONE) {
            return "상대위험 계산 불가 · 사유: " + reasonLabel(evidence.reasonCode());
        }
        String relativeRisk = "상대위험 " + decimal(evidence.relativeRisk(), 3);
        if (evidence.relativeRiskCiLower() == null && evidence.relativeRiskCiUpper() == null) {
            return relativeRisk + " · 95% CI는 계약상 산출하지 않음";
        }
        if (evidence.relativeRiskCiLower() == null || evidence.relativeRiskCiUpper() == null) {
            return relativeRisk + " · 95% CI 일부 미제공";
        }
        return relativeRisk + " · 95% CI " + decimal(evidence.relativeRiskCiLower(), 3)
                + "~" + decimal(evidence.relativeRiskCiUpper(), 3);
    }

    private static String percent(Double value) {
        return value == null ? "-" : String.format(Locale.ROOT, "%.2f%%", value * 100.0);
    }

    private static String percentageInterval(Double lower, Double upper) {
        if (lower == null || upper == null) return "-";
        return percent(lower) + "~" + percent(upper);
    }

    private static String decimal(Double value, int places) {
        if (value == null) return "-";
        return String.format(Locale.ROOT, "%." + places + "f", value);
    }

    private static String rangeStatusLabel(RangeStatus status) {
        return switch (status) {
            case TYPICAL -> "● 전형 범위";
            case CAUTION -> "▲ 주의 편차";
            case SEVERE -> "■ 심한 편차";
            case REFERENCE_ONLY, DATA_MISSING, UNREGISTERED_CONDITION, INSUFFICIENT_EVIDENCE ->
                    "? 근거 부족";
        };
    }

    private static String selectionReasonLabel(RangeSelectionReason reason) {
        if (reason == null) return "선택 근거 없음";
        return switch (reason) {
            case EXACT_CONTEXT -> "정확한 맥락 일치";
            case BAND_BOUNDARY_NOT_SEALED_FALLBACK -> "밴드 경계 미봉인 · 보수적 상위 기준 사용";
            case BAND_BOUNDARY_NOT_SEALED -> "밴드 경계 미봉인";
            case NO_BASELINE -> "기준선 없음";
            case UNREGISTERED_EQUIPMENT -> "미등록 설비";
            case UNREGISTERED_CONTEXT -> "미등록 맥락";
            case DATA_MISSING -> "데이터 누락";
        };
    }

    private static String ruleStatusLabel(RuleMatchStatus status) {
        return switch (status) {
            case MATCHED -> "규칙 일치";
            case NOT_MATCHED -> "규칙 불일치";
            case DATA_MISSING -> "규칙 입력 누락";
        };
    }

    private static String evidenceFamilyLabel(EvidenceFamily family) {
        return switch (family) {
            case STEEL_CHEMISTRY -> "제강 성분";
            case CASTING_STABILITY -> "연주 안정성";
            case CHARGE -> "장입";
            case FUEL_PROFILE -> "연료 구성";
            case PREHEAT -> "예열";
            case HEATING -> "가열";
            case SOAKING -> "균열";
            case RM4 -> "조압연 RM4";
            case DIMENSIONS -> "치수";
            case AP -> "AP 후행 품질";
        };
    }

    private static String reasonLabel(ReasonCode reason) {
        if (reason == null) return "사유 미제공";
        return switch (reason) {
            case NONE -> "계산 실패 사유 없음";
            case LOW_SUPPORT -> "표본 부족";
            case LOW_DEFECT_COUNT -> "불량 건수 부족";
            case ZERO_COMPARATOR_RISK -> "비교군 위험률 0";
            case NON_FINITE_ESTIMATE -> "유한하지 않은 추정값";
            case NO_INFORMATIVE_STRATA -> "유효 층 없음";
            case NO_VARIATION -> "값 변동 없음";
            case ZERO_VARIANCE -> "분산 0";
            case DIRECTION_NOT_REPEATED -> "방향성 재현 안 됨";
            case TOO_FEW_VALID_BOOTSTRAPS -> "유효 부트스트랩 부족";
            case NOT_APPLICABLE -> "해당 없음";
        };
    }

    private static String stageLabel(String stage) {
        return switch (stage) {
            case "CAST_RECORDED" -> "연주 기록";
            case "FURNACE_CHARGED" -> "가열로 장입";
            case "PREHEAT_COMPLETE" -> "예열 완료";
            case "HEAT_COMPLETE" -> "가열 완료";
            case "SOAK_COMPLETE" -> "균열 완료";
            case "FURNACE_EXTRACTED" -> "가열로 추출";
            case "RM4_RECORDED" -> "조압연 RM4 기록";
            case "AP_RECORDED_WITH_RESULT" -> "AP 후행 품질 확인";
            default -> stage;
        };
    }

    private static String equipmentLabel(String equipmentType, String equipmentId) {
        return equipmentTypeLabel(equipmentType) + " / " + equipmentId;
    }

    private static String equipmentTypeLabel(String equipmentType) {
        return switch (equipmentType) {
            case "SM_CC" -> "제강·연주";
            case "FURNACE" -> "가열로";
            case "RM4" -> "조압연 RM4";
            case "AP" -> "AP 후행 품질";
            default -> equipmentType;
        };
    }

    private static String definitionEquipment(String equipment) {
        int separator = equipment.indexOf(" / ");
        if (separator < 0) return equipment;
        return equipmentTypeLabel(equipment.substring(0, separator))
                + equipment.substring(separator);
    }

    private static String gradeFilterLabel(RiskGrade grade) {
        return switch (grade) {
            case CAUTION -> "주의";
            case DANGER -> "위험";
            case NORMAL -> "정상";
            case UNCONFIRMED, INSUFFICIENT_EVIDENCE -> "근거 부족";
        };
    }

    private static String alertStatus(HistoricalAlert alert) {
        if (alert.kind() == AlertKind.OPERATING_RANGE) {
            return switch (alert.rangeStatus()) {
                case TYPICAL -> "● 전형 범위";
                case CAUTION -> "▲ 주의 편차";
                case SEVERE -> "■ 심한 편차";
                case REFERENCE_ONLY, DATA_MISSING, UNREGISTERED_CONDITION, INSUFFICIENT_EVIDENCE ->
                        "? 근거 부족";
            };
        }
        return symbol(alert.grade());
    }

    private static String alertGradeFilterLabel(HistoricalAlert alert) {
        if (alert.kind() == AlertKind.OPERATING_RANGE) {
            return switch (alert.rangeStatus()) {
                case CAUTION -> "주의 편차";
                case SEVERE -> "심한 편차";
                case TYPICAL -> "전형 범위";
                case REFERENCE_ONLY, DATA_MISSING, UNREGISTERED_CONDITION, INSUFFICIENT_EVIDENCE ->
                        "근거 부족";
            };
        }
        return gradeFilterLabel(alert.grade());
    }

    private static int gradeFilterOrder(String label) {
        return switch (label) {
            case "주의", "주의 편차" -> 0;
            case "위험", "심한 편차" -> 1;
            default -> 2;
        };
    }

    private static List<EvidenceRow> evidenceRows(MaterialSnapshot material) {
        List<EvidenceRow> rows = new ArrayList<>();
        for (RangeEvaluation range : material.rangeEvaluations()) {
            String key = "R|" + material.materialKey() + "|" + range.field()
                    + "|" + value(range.selectedRuleId());
            rows.add(new EvidenceRow(key, List.of(
                    material.materialKey(), stageLabel(material.eventStage()),
                    "설비 운전범위", range.field(),
                    rangeStatusLabel(range.status()), value(range.observedValue()),
                    rangeCriteria(range), "-", "-",
                    "과거 분포 기준의 관측 위치이며 품질 원인을 뜻하지 않음")));
        }
        for (RuleEvaluation rule : material.ruleEvaluations()) {
            if (!rule.matched() && rule.status() != RuleMatchStatus.DATA_MISSING) continue;
            String key = "Q|" + material.materialKey() + "|" + rule.ruleId();
            rows.add(new EvidenceRow(key, List.of(
                    material.materialKey(), stageLabel(material.eventStage()),
                    "품질 위험 연결", shortId(rule.ruleId()),
                    ruleEvaluationStatus(rule), "-", ruleCriteria(rule),
                    metricText("발견구간", rule.discovery()),
                    metricText("확인구간 결과", rule.confirmation()),
                    rule.status() == RuleMatchStatus.DATA_MISSING
                            ? MISSING_QUALITY_NOTICE : ASSOCIATION_NOTICE)));
        }
        return rows;
    }

    private static String statusText(ReplayControllerState state) {
        if (state.status() == ReplayStatus.ERROR) {
            return "! 데이터 오류 · " + state.errorMessage();
        }
        String status = switch (state.status()) {
            case STOPPED -> "재생 정지";
            case RUNNING -> "재생 중";
            case PAUSED -> "재생 일시정지";
            case COMPLETED -> "재생 완료";
            case CLOSED -> "재생 종료";
            case ERROR -> throw new IllegalStateException("handled above");
        };
        return status + " · " + state.speed() + " · " + number(state.unitsDelivered()) + " 시간대";
    }

    private void renderReplayState(ReplayControllerState state) {
        replayStatus.setText(statusText(state));
        if (state.status() != ReplayStatus.ERROR) {
            replayFailureDetails.setText(" ");
            return;
        }
        String location = state.failureContext() == null
                ? "실패 위치 확인 불가"
                : "실패 위치 · " + replayTime(
                        state.failureContext().replayDate(), state.failureContext().replayHour())
                        + " · 단계 " + stageLabel(state.failureContext().batchStep())
                        + " · 소재 " + String.join(", ", state.failureContext().materialKeys());
        String transitions = state.statusHistory().stream()
                .map(Enum::name)
                .collect(java.util.stream.Collectors.joining(" → "));
        replayFailureDetails.setText(location + " · 상태전이 " + transitions);
    }

    private static String symbol(RiskGrade grade) {
        return switch (grade) {
            case NORMAL -> "● 정상";
            case CAUTION -> "▲ 주의";
            case DANGER -> "■ 위험";
            case UNCONFIRMED, INSUFFICIENT_EVIDENCE -> "? 근거 부족";
        };
    }

    private static String qualityStatus(MaterialSnapshot material, boolean historicalEvidenceOnly) {
        RiskGrade grade = historicalEvidenceOnly
                ? material.historicalEvidenceRisk()
                : material.qualityRisk();
        if (grade == RiskGrade.CAUTION || grade == RiskGrade.DANGER) {
            return symbol(grade);
        }
        boolean dataMissing = material.ruleEvaluations().stream()
                .filter(value -> value.historicalEvidenceOnly() == historicalEvidenceOnly)
                .anyMatch(value -> value.status() == RuleMatchStatus.DATA_MISSING);
        return dataMissing ? "? 데이터 누락" : symbol(grade);
    }

    private static String ruleEvaluationStatus(RuleEvaluation evaluation) {
        return evaluation.status() == RuleMatchStatus.DATA_MISSING
                ? "? 데이터 누락"
                : symbol(evaluation.grade());
    }

    private static String riskExplanation(RiskGrade grade) {
        return switch (grade) {
            case NORMAL -> "현재까지 공개된 과거 공정값에서 품질 위험 연결 근거 없음";
            case CAUTION, DANGER -> ASSOCIATION_NOTICE;
            case UNCONFIRMED, INSUFFICIENT_EVIDENCE -> "판정에 필요한 공개 근거가 충분하지 않음";
        };
    }

    private static String replayTime(String date, Integer hour) {
        return hour == null ? date : date + " " + String.format(Locale.ROOT, "%02d시", hour);
    }

    private static String value(Object value) {
        if (value == null) return "-";
        if (value instanceof com.sfep.equipmentmonitor.risk.RiskScalar scalar) {
            return value(scalar.value());
        }
        if (value instanceof java.math.BigDecimal decimal) return decimal.toPlainString();
        return value.toString();
    }

    private static String shortId(String id) {
        return id == null || id.length() <= 20 ? value(id) : id.substring(0, 20) + "…";
    }

    private static String number(long value) {
        return NumberFormat.getIntegerInstance(Locale.KOREA).format(value);
    }

    private static String message(RuntimeException error) {
        return error.getMessage() == null ? error.getClass().getSimpleName() : error.getMessage();
    }

    private static ReadOnlyTableModel model(String... columns) {
        return new ReadOnlyTableModel(columns);
    }

    private static void requireEdt() {
        if (!SwingUtilities.isEventDispatchThread()) {
            throw new IllegalStateException("Swing state must be updated on the EDT");
        }
    }

    private record EvidenceRow(String key, List<Object> values) {
        private EvidenceRow {
            values = List.copyOf(values);
        }
    }

    private static final class PendingUpdates {
        private final LinkedHashMap<String, MaterialSnapshot> changedMaterials = new LinkedHashMap<>();
        private final LinkedHashMap<String, MaterialSnapshot> lastChangedMaterials = new LinkedHashMap<>();
        private final LinkedHashMap<EquipmentKey, EquipmentSnapshot> lastChangedEquipment = new LinkedHashMap<>();
        private final LinkedHashMap<String, EvidenceRow> evidenceRows = new LinkedHashMap<>();
        private final List<HistoricalAlert> alerts = new ArrayList<>();
        private ReplayUnit lastUnit;
        private long unitsProcessed;
        private long eventsProcessed;

        private void merge(MonitorUpdate update) {
            lastUnit = update.unit();
            unitsProcessed = update.unitsProcessed();
            eventsProcessed = update.eventsProcessed();
            lastChangedMaterials.clear();
            lastChangedMaterials.putAll(update.changedMaterials());
            lastChangedEquipment.clear();
            lastChangedEquipment.putAll(update.changedEquipment());
            update.changedMaterials().forEach((key, material) -> {
                changedMaterials.put(key, material);
                evidenceRows(material).forEach(row -> {
                    evidenceRows.remove(row.key());
                    evidenceRows.put(row.key(), row);
                    while (evidenceRows.size() > MAX_RETAINED_EVIDENCE_ROWS) {
                        evidenceRows.remove(evidenceRows.keySet().iterator().next());
                    }
                });
            });
            alerts.addAll(update.newAlerts());
        }
    }

    private static final class ReadOnlyTableModel extends DefaultTableModel {
        private ReadOnlyTableModel(String... columns) {
            super(columns, 0);
        }

        @Override
        public boolean isCellEditable(int row, int column) {
            return false;
        }

        private void addRows(List<Object[]> rows) {
            if (rows.isEmpty()) return;
            int first = getRowCount();
            for (Object[] row : rows) {
                Vector<Object> values = new Vector<>(row.length);
                java.util.Collections.addAll(values, row);
                dataVector.add(values);
            }
            fireTableRowsInserted(first, getRowCount() - 1);
        }

        private void applyRows(Map<Integer, Object[]> replacements, List<Object[]> additions) {
            if (replacements.isEmpty() && additions.isEmpty()) return;
            replacements.forEach((row, values) -> dataVector.set(row, vector(values)));
            int firstAddition = getRowCount();
            additions.forEach(values -> dataVector.add(vector(values)));
            if (replacements.isEmpty()) {
                fireTableRowsInserted(firstAddition, getRowCount() - 1);
            } else {
                fireTableDataChanged();
            }
        }

        private void replaceRows(List<Object[]> rows) {
            dataVector.clear();
            for (Object[] row : rows) dataVector.add(vector(row));
            fireTableDataChanged();
        }

        private static Vector<Object> vector(Object[] row) {
            Vector<Object> values = new Vector<>(row.length);
            java.util.Collections.addAll(values, row);
            return values;
        }
    }
}
