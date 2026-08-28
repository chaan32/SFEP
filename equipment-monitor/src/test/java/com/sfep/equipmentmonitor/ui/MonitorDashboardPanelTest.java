package com.sfep.equipmentmonitor.ui;

import com.fasterxml.jackson.databind.node.JsonNodeFactory;
import com.fasterxml.jackson.databind.node.ObjectNode;
import com.sfep.equipmentmonitor.alert.AlertKey;
import com.sfep.equipmentmonitor.alert.AlertKind;
import com.sfep.equipmentmonitor.alert.HistoricalAlert;
import com.sfep.equipmentmonitor.replay.ReplayControllerState;
import com.sfep.equipmentmonitor.replay.ReplayEvent;
import com.sfep.equipmentmonitor.replay.ReplayFailureContext;
import com.sfep.equipmentmonitor.replay.ReplaySpeed;
import com.sfep.equipmentmonitor.replay.ReplayStatus;
import com.sfep.equipmentmonitor.replay.ReplayUnit;
import com.sfep.equipmentmonitor.risk.MetricEvidence;
import com.sfep.equipmentmonitor.risk.EvidenceFamily;
import com.sfep.equipmentmonitor.risk.FieldRole;
import com.sfep.equipmentmonitor.risk.RangeEvaluation;
import com.sfep.equipmentmonitor.risk.RangeSelectionReason;
import com.sfep.equipmentmonitor.risk.RangeStatus;
import com.sfep.equipmentmonitor.risk.ReasonCode;
import com.sfep.equipmentmonitor.risk.RiskGrade;
import com.sfep.equipmentmonitor.risk.RiskScalar;
import com.sfep.equipmentmonitor.risk.RuleEvaluation;
import com.sfep.equipmentmonitor.risk.RuleMatchStatus;
import com.sfep.equipmentmonitor.state.EquipmentKey;
import com.sfep.equipmentmonitor.state.EquipmentSnapshot;
import com.sfep.equipmentmonitor.state.MaterialSnapshot;
import com.sfep.equipmentmonitor.state.MonitorUpdate;
import org.junit.jupiter.api.Test;

import javax.swing.AbstractButton;
import javax.swing.JButton;
import javax.swing.JComboBox;
import javax.swing.JComponent;
import javax.swing.JLabel;
import javax.swing.JPanel;
import javax.swing.JTabbedPane;
import javax.swing.JTable;
import javax.swing.SwingUtilities;
import javax.swing.event.TableModelEvent;
import java.awt.Color;
import java.awt.Component;
import java.awt.Container;
import java.time.Duration;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import java.util.concurrent.CopyOnWriteArrayList;
import java.util.concurrent.atomic.AtomicReference;

import static org.assertj.core.api.Assertions.assertThat;
import static org.junit.jupiter.api.Assertions.assertTimeout;

class MonitorDashboardPanelTest {
    private static final String BUNDLE = "sha256:" + "a".repeat(64);
    private static final String CRITERIA = "sha256:" + "b".repeat(64);

    @Test
    void presentsRiskSummaryCardsThatUpdateFromTheLatestMaterialStates() throws Exception {
        Fixture fixture = panel();

        fixture.panel.acceptUpdate(overviewRiskUpdate());
        onEdt(() -> { });

        assertThat(onEdt(() -> component(
                fixture.panel, "summary-severe-value", JLabel.class).getText())).isEqualTo("1");
        assertThat(onEdt(() -> component(
                fixture.panel, "summary-caution-value", JLabel.class).getText())).isEqualTo("1");
        assertThat(onEdt(() -> component(
                fixture.panel, "summary-quality-danger-value", JLabel.class).getText())).isEqualTo("1");
        assertThat(onEdt(() -> component(
                fixture.panel, "summary-material-value", JLabel.class).getText())).isEqualTo("4");
        assertThat(onEdt(() -> visibleText(component(
                fixture.panel, "priority-risk-panel", JPanel.class))))
                .contains("■ 심한 설비 편차", "▲ 주의 설비 편차", "■ 조기 품질 위험", "재생된 소재");
    }

    @Test
    void putsTheMostUrgentMaterialAtTheTopWithoutChangingOverviewColumns() throws Exception {
        Fixture fixture = panel();
        fixture.panel.acceptUpdate(overviewRiskUpdate());
        onEdt(() -> { });
        JTable overview = onEdt(() -> component(fixture.panel, "overview-table", JTable.class));

        assertThat(onEdt(() -> overview.getValueAt(0, columnOf(overview, "소재"))))
                .isEqualTo("mat-danger");
        assertThat(columnNames(overview)).containsExactly(
                "설비 운전범위", "조기 품질 위험", "AP 후행 품질 확인",
                "소재", "공정 단계", "설비", "재생 시각");
    }

    @Test
    void explainsTheSelectedMaterialWithPlainLanguageAndAnExplicitAction() throws Exception {
        Fixture fixture = panel();
        fixture.panel.acceptUpdate(overviewRiskUpdate());
        onEdt(() -> { });
        JTable overview = onEdt(() -> component(fixture.panel, "overview-table", JTable.class));

        onEdt(() -> overview.setRowSelectionInterval(0, 0));

        String detail = onEdt(() -> component(
                fixture.panel, "overview-selection-detail", JLabel.class).getText());
        assertThat(detail).contains(
                "mat-danger", "즉시 확인 필요", "설비 운전범위: 심한 편차", "조기 품질 위험: 위험");
    }

    @Test
    void treatsMissingApFeedbackAsEvidenceThatNeedsReview() throws Exception {
        Fixture fixture = panel();
        fixture.panel.acceptUpdate(overviewRiskUpdate());
        onEdt(() -> { });
        JTable overview = onEdt(() -> component(fixture.panel, "overview-table", JTable.class));

        int normalRow = rowOf(overview, "소재", "mat-normal");
        onEdt(() -> overview.setRowSelectionInterval(normalRow, normalRow));

        String detail = onEdt(() -> component(
                fixture.panel, "overview-selection-detail", JLabel.class).getText());
        assertThat(detail).contains(
                "mat-normal", "판정 근거 확인 필요", "AP 후행 품질: 근거 부족");
    }

    @Test
    void appliesPoscoPrimaryColourAndComfortableTableDensity() throws Exception {
        Fixture fixture = panel();
        JButton start = onEdt(() -> componentWithText(fixture.panel, "시작", JButton.class));
        JPanel brandHeader = onEdt(() -> component(
                fixture.panel, "brand-header", JPanel.class));
        JTable overview = onEdt(() -> component(fixture.panel, "overview-table", JTable.class));

        assertThat(onEdt(() -> start.getBackground())).isEqualTo(Color.decode("#05507D"));
        assertThat(onEdt(() -> start.getForeground())).isEqualTo(Color.WHITE);
        assertThat(onEdt(() -> brandHeader.getBackground())).isEqualTo(Color.decode("#05507D"));
        assertThat(onEdt(() -> overview.getRowHeight())).isGreaterThanOrEqualTo(36);
        assertThat(onEdt(() -> overview.getShowHorizontalLines())).isFalse();
        assertThat(onEdt(() -> overview.getShowVerticalLines())).isFalse();
    }

    @Test
    void rendersHistoricalReplayModeFiveTabsAndSealedManagementMetadataWithoutLiveClaims() throws Exception {
        Fixture fixture = panel();

        String visible = onEdt(() -> visibleText(fixture.panel));
        JTabbedPane tabs = onEdt(() -> component(fixture.panel, "monitor-tabs", JTabbedPane.class));

        assertThat(visible).contains("과거 데이터 재생 · HISTORICAL_REPLAY");
        assertThat(tabTitles(tabs)).containsExactly("전체 현황", "설비 상세", "위험 근거", "이력 조회", "관리 기준");
        assertThat(visible).contains("● 정상", "▲ 주의", "■ 위험", "? 근거 부족", "! 데이터 오류");
        assertThat(visible).contains(BUNDLE, CRITERIA, "189,043", "424", "313", "2025-01-01 ~ 2025-03-31");
        assertThat(visible).doesNotContain("실시간", "현재 공장 상태", "고장예측");
    }

    @Test
    void routesEveryReplayControlIncludingNextDateAndSpeedThroughInjectedControl() throws Exception {
        Fixture fixture = panel();

        onEdt(() -> {
            click(fixture.panel, "시작");
            click(fixture.panel, "일시정지");
            click(fixture.panel, "재개");
            click(fixture.panel, "한 단계");
            click(fixture.panel, "정지");
            click(fixture.panel, "다음 날짜");
            JComboBox<?> speeds = component(fixture.panel, "replay-speed", JComboBox.class);
            speeds.setSelectedItem(ReplaySpeed.X100);
        });

        assertThat(fixture.control.actions).containsExactly(
                "start", "pause", "resume", "step", "stop", "next-date", "speed:X100");
    }

    @Test
    void appliesBackgroundMonitorUpdatesOnEdtAndDisclosesUnorderedSameTimeMaterials() throws Exception {
        Fixture fixture = panel();
        JTable overview = onEdt(() -> component(fixture.panel, "overview-table", JTable.class));
        List<Boolean> eventThreads = new CopyOnWriteArrayList<>();
        onEdt(() -> overview.getModel().addTableModelListener(
                ignored -> eventThreads.add(SwingUtilities.isEventDispatchThread())));

        MonitorUpdate update = update(false, List.of());
        Thread worker = new Thread(() -> fixture.panel.acceptUpdate(update), "test-replay-worker");
        worker.start();
        worker.join();
        onEdt(() -> { });

        String visible = onEdt(() -> visibleText(fixture.panel));
        assertThat(onEdt(overview::getRowCount)).isEqualTo(2);
        assertThat(eventThreads).isNotEmpty().allMatch(Boolean::booleanValue);
        assertThat(visible).contains("시간대 처리 소재 2건, 정확한 처리순서 없음");
        assertThat(visible).contains("2025-02-03 14시");
    }

    @Test
    void explainsQualityAlertsAsHistoricalAssociationsWithoutClaimingDirectCausation() throws Exception {
        Fixture fixture = panel();
        fixture.panel.acceptUpdate(update(false, List.of(alert())));
        onEdt(() -> { });

        String visible = onEdt(() -> visibleText(fixture.panel));
        JTable history = onEdt(() -> component(fixture.panel, "history-table", JTable.class));

        assertThat(onEdt(history::getRowCount)).isEqualTo(1);
        assertThat(visible).contains("과거 품질 결과와 통계적으로 연결된 위험 근거");
        assertThat(visible).contains("직접 원인으로 단정하지 않음");
    }

    @Test
    void displaysControllerFailureWithNonColorErrorSymbol() throws Exception {
        Fixture fixture = panel();
        fixture.control.state.set(new ReplayControllerState(
                ReplayStatus.ERROR, true, ReplaySpeed.X1, 7, "2025-02-03",
                new ReplayFailureContext(
                        "batch-7", "FURNACE_CHARGED", "2025-02-03", 14,
                        List.of("mat-1", "mat-2")),
                List.of(ReplayStatus.STOPPED, ReplayStatus.RUNNING, ReplayStatus.ERROR),
                "csv damaged"));

        onEdt(fixture.panel::refreshReplayState);

        JLabel status = onEdt(() -> component(fixture.panel, "replay-status", JLabel.class));
        JLabel details = onEdt(() -> component(
                fixture.panel, "replay-failure-details", JLabel.class));
        assertThat(onEdt(status::getText)).isEqualTo("! 데이터 오류 · csv damaged");
        assertThat(onEdt(details::getText)).contains(
                "실패 위치 · 2025-02-03 14시", "단계 가열로 장입",
                "소재 mat-1, mat-2", "STOPPED → RUNNING → ERROR");
    }

    @Test
    void reportsEdtApplicationFailureBackToReplayWithTheAffectedUnit() throws Exception {
        Fixture fixture = panel();
        fixture.control.failNextStateRead = true;

        fixture.panel.acceptUpdate(update(false, List.of()));
        onEdt(() -> { });

        assertThat(fixture.control.actions).contains("ui-failure:batch-1");
        assertThat(fixture.control.state.get().status()).isEqualTo(ReplayStatus.ERROR);
        assertThat(fixture.control.state.get().failureContext().materialKeys())
                .containsExactly("mat-1", "mat-2");
        JLabel details = onEdt(() -> component(
                fixture.panel, "replay-failure-details", JLabel.class));
        assertThat(onEdt(details::getText)).contains(
                "단계 가열로 장입", "소재 mat-1, mat-2", "STOPPED → ERROR");
    }

    @Test
    void separatesOperatingRangeEarlyQualityAndApFeedbackWithRangeStatusPrecedence() throws Exception {
        Fixture fixture = panel();
        fixture.panel.acceptUpdate(overviewRiskUpdate());
        onEdt(() -> { });
        JTable overview = onEdt(() -> component(fixture.panel, "overview-table", JTable.class));

        assertThat(columnNames(overview)).containsExactly(
                "설비 운전범위", "조기 품질 위험", "AP 후행 품질 확인",
                "소재", "공정 단계", "설비", "재생 시각");
        assertOverview(overview, "mat-normal", "● 전형 범위", "● 정상", "? 근거 부족");
        assertOverview(overview, "mat-missing", "? 근거 부족", "? 근거 부족", "? 근거 부족");
        assertOverview(overview, "mat-caution", "▲ 주의 편차", "▲ 주의", "● 정상");
        assertOverview(overview, "mat-danger", "■ 심한 편차", "■ 위험", "▲ 주의");
        assertThat(tableRowText(overview, rowOf(overview, "소재", "mat-danger")))
                .contains("가열로 장입", "가열로 / FURNACE_1");
    }

    @Test
    void showsSealedDiscoveryAndConfirmationStatisticsAndRangeSelectionEvidence() throws Exception {
        Fixture fixture = panel();
        fixture.panel.acceptUpdate(evidenceUpdate());
        onEdt(() -> { });
        JTable evidence = onEdt(() -> component(fixture.panel, "evidence-table", JTable.class));

        int rangeRow = rowOf(evidence, "구분", "설비 운전범위");
        int qualityRow = rowOf(evidence, "구분", "품질 위험 연결");
        assertThat(tableRowText(evidence, rangeRow)).contains(
                "■ 심한 편차", "맥락 수준 2", "정확한 맥락 일치", "가열로 장입");
        assertThat(tableRowText(evidence, qualityRow)).contains(
                "가열 근거군", "규칙 일치", "조기 품질 위험 대상",
                "표본 100 / 불량 20",
                "보정 불량률 18.00%",
                "관측 불량률 20.00% · Wilson 95% CI 12.00%~28.00%",
                "비교군 보정 불량률 8.00%",
                "상대위험 2.250 · 95% CI 1.500~3.100",
                "q-value 0.0200",
                "확인구간 결과 · 표본 40 / 불량 3",
                "상대위험 계산 불가 · 사유: 표본 부족",
                "q-value -",
                "직접 원인으로 단정하지 않음");
    }

    @Test
    void filtersAllStoredHistoryByDateEquipmentMaterialAndGrade() throws Exception {
        Fixture fixture = panel();
        HistoricalAlert furnaceDanger = alert(
                "2025-02-03", "mat-2", "FURNACE", "FURNACE_1", RiskGrade.DANGER, "d");
        HistoricalAlert rm4Caution = alert(
                "2025-02-04", "mat-1", "RM4", "RM4", RiskGrade.CAUTION, "e");
        fixture.panel.acceptUpdate(update(false, List.of(furnaceDanger, rm4Caution)));
        onEdt(() -> { });

        JTable history = onEdt(() -> component(fixture.panel, "history-table", JTable.class));
        JComboBox<?> date = onEdt(() -> component(fixture.panel, "history-date-filter", JComboBox.class));
        JComboBox<?> equipment = onEdt(() -> component(
                fixture.panel, "history-equipment-filter", JComboBox.class));
        JComboBox<?> material = onEdt(() -> component(
                fixture.panel, "history-material-filter", JComboBox.class));
        JComboBox<?> grade = onEdt(() -> component(fixture.panel, "history-grade-filter", JComboBox.class));

        assertThat(onEdt(history::getRowCount)).isEqualTo(2);
        assertThat(comboItems(date)).containsExactly("전체", "2025-02-03", "2025-02-04");
        assertThat(comboItems(equipment)).containsExactly("전체", "가열로 / FURNACE_1", "조압연 RM4 / RM4");
        assertThat(comboItems(material)).containsExactly("전체", "mat-1", "mat-2");
        assertThat(comboItems(grade)).containsExactly("전체", "주의", "위험");

        onEdt(() -> date.setSelectedItem("2025-02-04"));
        assertThat(onEdt(history::getRowCount)).isEqualTo(1);
        assertThat(tableRowText(history, 0)).contains("mat-1", "▲ 주의", "조압연 RM4 / RM4");

        onEdt(() -> {
            date.setSelectedItem("전체");
            equipment.setSelectedItem("가열로 / FURNACE_1");
            material.setSelectedItem("mat-2");
            grade.setSelectedItem("위험");
        });
        assertThat(onEdt(history::getRowCount)).isEqualTo(1);
        assertThat(tableRowText(history, 0)).contains("mat-2", "■ 위험", "2025-02-03");

        onEdt(() -> material.setSelectedItem("mat-1"));
        assertThat(onEdt(history::getRowCount)).isZero();
    }

    @Test
    void coalescesBackgroundUpdatesIntoOneScheduledDrainWithoutLosingOrderFinalStateOrAlerts() throws Exception {
        RecordingUpdateScheduler scheduler = new RecordingUpdateScheduler();
        Fixture fixture = panel(scheduler);

        for (int sequence = 1; sequence <= 100; sequence++) {
            fixture.panel.acceptUpdate(sequencedUpdate(sequence, List.of(largeAlert(sequence))));
        }

        assertThat(scheduler.pendingCount()).isEqualTo(1);
        onEdt(scheduler::runNext);
        JTable overview = onEdt(() -> component(fixture.panel, "overview-table", JTable.class));
        JTable history = onEdt(() -> component(fixture.panel, "history-table", JTable.class));
        String visible = onEdt(() -> visibleText(fixture.panel));

        assertThat(onEdt(overview::getRowCount)).isEqualTo(100);
        assertThat(onEdt(history::getRowCount)).isEqualTo(100);
        assertThat(history.getValueAt(0, columnOf(history, "소재"))).isEqualTo("mat-00001");
        assertThat(history.getValueAt(99, columnOf(history, "소재"))).isEqualTo("mat-00100");
        assertThat(visible).contains("처리 · 100 시간대 / 100 이벤트");
        assertThat(scheduler.pendingCount()).isZero();
    }

    @Test
    void publishesOneOverviewModelEventForOneCoalescedMaterialBatch() throws Exception {
        RecordingUpdateScheduler scheduler = new RecordingUpdateScheduler();
        Fixture fixture = panel(scheduler);
        JTable overview = onEdt(() -> component(fixture.panel, "overview-table", JTable.class));
        List<TableModelEvent> modelEvents = new CopyOnWriteArrayList<>();
        onEdt(() -> overview.getModel().addTableModelListener(modelEvents::add));

        for (int sequence = 1; sequence <= 100; sequence++) {
            fixture.panel.acceptUpdate(sequencedUpdate(sequence, List.of()));
        }
        onEdt(scheduler::runNext);

        assertThat(modelEvents).hasSize(1);
        assertThat(modelEvents.getFirst().getType()).isEqualTo(TableModelEvent.INSERT);
        assertThat(modelEvents.getFirst().getFirstRow()).isZero();
        assertThat(modelEvents.getFirst().getLastRow()).isEqualTo(99);
    }

    @Test
    void preservesSelectedMaterialAndSorterWhenABatchChangesItsPriority() throws Exception {
        RecordingUpdateScheduler scheduler = new RecordingUpdateScheduler();
        Fixture fixture = panel(scheduler);
        fixture.panel.acceptUpdate(sequencedUpdate(1, RiskGrade.NORMAL));
        fixture.panel.acceptUpdate(sequencedUpdate(2, RiskGrade.NORMAL));
        onEdt(scheduler::runNext);
        JTable overview = onEdt(() -> component(fixture.panel, "overview-table", JTable.class));
        Object originalModel = overview.getModel();
        Object originalSorter = overview.getRowSorter();
        int selectedRow = rowOf(overview, "소재", "mat-00002");
        onEdt(() -> overview.setRowSelectionInterval(selectedRow, selectedRow));
        List<TableModelEvent> modelEvents = new CopyOnWriteArrayList<>();
        onEdt(() -> overview.getModel().addTableModelListener(modelEvents::add));

        fixture.panel.acceptUpdate(sequencedUpdate(2, RiskGrade.DANGER));
        for (int sequence = 3; sequence <= 52; sequence++) {
            fixture.panel.acceptUpdate(sequencedUpdate(sequence, RiskGrade.NORMAL));
        }
        onEdt(scheduler::runNext);

        assertThat(modelEvents).hasSize(1);
        assertThat(modelEvents.getFirst().getType()).isEqualTo(TableModelEvent.UPDATE);
        assertThat(modelEvents.getFirst().getFirstRow()).isZero();
        assertThat(modelEvents.getFirst().getLastRow()).isEqualTo(Integer.MAX_VALUE);
        assertThat(overview.getModel()).isSameAs(originalModel);
        assertThat(overview.getRowSorter()).isSameAs(originalSorter);
        assertThat(overview.getValueAt(
                overview.getSelectedRow(), columnOf(overview, "소재"))).isEqualTo("mat-00002");
        assertThat(overview.getValueAt(0, columnOf(overview, "소재"))).isEqualTo("mat-00002");
        assertThat(rowOf(overview, "소재", "mat-00003"))
                .isLessThan(rowOf(overview, "소재", "mat-00052"));
    }

    @Test
    void preservesFirstSeenUnitOrderWhenCoalescingLatestMaterialState() throws Exception {
        RecordingUpdateScheduler scheduler = new RecordingUpdateScheduler();
        Fixture fixture = panel(scheduler);

        fixture.panel.acceptUpdate(sequencedUpdate(2, List.of()));
        fixture.panel.acceptUpdate(sequencedUpdate(1, List.of()));
        onEdt(scheduler::runNext);

        JTable overview = onEdt(() -> component(fixture.panel, "overview-table", JTable.class));
        assertThat(overview.getValueAt(0, columnOf(overview, "소재"))).isEqualTo("mat-00002");
        assertThat(overview.getValueAt(1, columnOf(overview, "소재"))).isEqualTo("mat-00001");
    }

    @Test
    void discardsPendingUpdatesAfterPanelRemoval() throws Exception {
        RecordingUpdateScheduler scheduler = new RecordingUpdateScheduler();
        Fixture fixture = panel(scheduler);
        fixture.panel.acceptUpdate(sequencedUpdate(1, List.of(largeAlert(1))));

        onEdt(fixture.panel::removeNotify);
        onEdt(scheduler::runNext);

        JTable overview = onEdt(() -> component(fixture.panel, "overview-table", JTable.class));
        JTable history = onEdt(() -> component(fixture.panel, "history-table", JTable.class));
        assertThat(onEdt(overview::getRowCount)).isZero();
        assertThat(onEdt(history::getRowCount)).isZero();
    }

    @Test
    void incrementallyRetainsAllElevenThousandEightHundredTwentySevenAlertsWithinBound() throws Exception {
        Fixture fixture = panel();
        List<HistoricalAlert> alerts = new ArrayList<>(11_827);
        for (int index = 1; index <= 11_827; index++) alerts.add(largeAlert(index));

        assertTimeout(Duration.ofSeconds(10), () -> {
            fixture.panel.acceptUpdate(sequencedUpdate(1, alerts));
            onEdt(() -> { });
        });

        JTable history = onEdt(() -> component(fixture.panel, "history-table", JTable.class));
        JTable evidence = onEdt(() -> component(fixture.panel, "evidence-table", JTable.class));
        JComboBox<?> materials = onEdt(() -> component(
                fixture.panel, "history-material-filter", JComboBox.class));
        assertThat(onEdt(history::getRowCount)).isEqualTo(11_827);
        assertThat(onEdt(evidence::getRowCount))
                .isPositive()
                .isLessThanOrEqualTo(MonitorDashboardPanel.MAX_RETAINED_EVIDENCE_ROWS);
        assertThat(materials.getItemCount()).isEqualTo(11_828);
    }

    @Test
    void retainsFullAlertEvidenceAndPriorRiskRowsAfterLaterUnits() throws Exception {
        Fixture fixture = panel();
        HistoricalAlert quality = largeAlert(5);
        HistoricalAlert operating = operatingAlert(6);

        fixture.panel.acceptUpdate(evidenceUpdateWithAlerts(List.of(quality, operating)));
        fixture.panel.acceptUpdate(sequencedUpdate(2, List.of()));
        onEdt(() -> { });

        JTable history = onEdt(() -> component(fixture.panel, "history-table", JTable.class));
        JTable evidence = onEdt(() -> component(fixture.panel, "evidence-table", JTable.class));
        JComboBox<?> grade = onEdt(() -> component(fixture.panel, "history-grade-filter", JComboBox.class));

        assertThat(columnNames(history)).contains(
                "필드", "관측값", "운전범위 판정", "반복", "발견구간 통계", "확인구간 통계");
        assertThat(tableRowText(history, rowOf(history, "소재", "mat-00005"))).contains(
                "■ 위험", "f_temp", "1270", "반복 확인 필요",
                "표본 100 / 불량 20", "상대위험 2.250", "q-value 0.0200");
        assertThat(tableRowText(history, rowOf(history, "소재", "mat-00006"))).contains(
                "■ 심한 편차", "반복 아님");
        assertThat(comboItems(grade)).contains("위험", "심한 편차");
        assertThat(comboItems(grade)).doesNotHaveDuplicates();
        assertThat(rowOf(evidence, "소재", "mat-evidence")).isGreaterThanOrEqualTo(0);
        assertThat(tableRowText(evidence, rowOf(evidence, "소재", "mat-evidence")))
                .contains("■ 심한 편차");
    }

    @Test
    void showsEveryMaterialInCurrentEquipmentUnitWithRepresentativeValuesAndThreeAxes() throws Exception {
        Fixture fixture = panel();
        fixture.panel.acceptUpdate(fiveMaterialEquipmentUpdate());
        onEdt(() -> { });

        JTable summary = onEdt(() -> component(fixture.panel, "equipment-table", JTable.class));
        JTable detail = onEdt(() -> component(fixture.panel, "equipment-material-table", JTable.class));

        assertThat(onEdt(summary::getRowCount)).isEqualTo(1);
        assertThat(onEdt(detail::getRowCount)).isEqualTo(5);
        assertThat(columnNames(detail)).containsExactly(
                "설비", "소재", "공개 대표값", "설비 운전범위",
                "조기 품질 위험", "AP 후행 품질 확인", "시간대 내부 순서");
        assertThat(tableRowText(detail, rowOf(detail, "소재", "mat-eq-5"))).contains(
                "f_temp=1205", "■ 심한 편차", "■ 위험", "▲ 주의", "정확한 순서 없음");
        String allDetails = tableText(detail);
        assertThat(allDetails).contains("mat-eq-1", "mat-eq-2", "mat-eq-3", "mat-eq-4", "mat-eq-5");
        assertThat(allDetails).doesNotContain("…");
    }

    @Test
    void replacesEquipmentSummaryWhenTheCurrentUnitMovesToAnotherEquipment() throws Exception {
        Fixture fixture = panel();
        fixture.panel.acceptUpdate(update(false, List.of()));
        fixture.panel.acceptUpdate(rm4EquipmentUpdate());
        onEdt(() -> { });

        JTable summary = onEdt(() -> component(fixture.panel, "equipment-table", JTable.class));
        JTable detail = onEdt(() -> component(
                fixture.panel, "equipment-material-table", JTable.class));

        assertThat(onEdt(summary::getRowCount)).isEqualTo(1);
        assertThat(tableText(summary)).contains("조압연 RM4", "RM4", "mat-rm4");
        assertThat(tableText(summary)).doesNotContain("FURNACE_1");
        assertThat(onEdt(detail::getRowCount)).isEqualTo(1);
        assertThat(tableText(detail)).contains("mat-rm4").doesNotContain("mat-1", "mat-2");
    }

    @Test
    void showsAccurateHoldoutDynamicReplayCountVerifiedFactsAndDefinitionIntervals() throws Exception {
        Fixture fixture = panel();
        JTable management = onEdt(() -> component(
                fixture.panel, "management-table", JTable.class));
        JTable definitions = onEdt(() -> component(
                fixture.panel, "management-definitions-table", JTable.class));

        assertThat(columnNames(management)).containsExactly("구분", "항목", "봉인된 값");
        assertThat(tableRowText(management, rowOf(
                management, "항목", "독립 평가(holdout) 대상"))).contains("23,631");
        assertThat(tableRowText(management, rowOf(
                management, "항목", "현재까지 재생된 고유 소재 수 (완료 시 전체)"))).contains("0");
        assertThat(tableText(management)).contains(
                "원천 데이터 해시 | 가열·열연 | sha256:source",
                "산출물 해시 | replay_events.csv | sha256:artifact",
                "격리 건수 | 중복 AP 키 | 2");
        assertThat(tableText(management)).doesNotContain("분석 소재 건수");
        assertThat(onEdt(definitions::getRowCount)).isEqualTo(2);
        assertThat(columnNames(definitions)).containsExactly(
                "구분", "규칙 ID", "최초 공개 단계", "설비", "대상", "기준 수준", "정의/위험구간");
        assertThat(tableText(definitions)).contains(
                "설비 운전범위 | range-rule", "p01=1000, p99=1300",
                "품질 위험구간 | quality-rule", "{\"allOf\":[]}");

        fixture.panel.acceptUpdate(update(false, List.of()));
        onEdt(() -> { });

        assertThat(tableRowText(management, rowOf(
                management, "항목", "현재까지 재생된 고유 소재 수 (완료 시 전체)"))).contains("2");
    }

    private static Fixture panel() throws Exception {
        return panel(SwingUtilities::invokeLater);
    }

    private static Fixture panel(MonitorDashboardPanel.UpdateScheduler scheduler) throws Exception {
        MonitorDashboardMetadata metadata = new MonitorDashboardMetadata(
                BUNDLE,
                CRITERIA,
                "2025-04-01T00:00:00Z",
                "HISTORICAL_REPLAY",
                "2025-01-01",
                "2025-03-31",
                23_631,
                189_043,
                424,
                313,
                List.of(
                        new MonitorDashboardMetadata.ManagementFact(
                                "원천 데이터 해시", "가열·열연", "sha256:source"),
                        new MonitorDashboardMetadata.ManagementFact(
                                "산출물 해시", "replay_events.csv", "sha256:artifact"),
                        new MonitorDashboardMetadata.ManagementFact(
                                "격리 건수", "중복 AP 키", "2")),
                List.of(
                        new MonitorDashboardMetadata.DefinitionView(
                                "설비 운전범위", "range-rule", "FURNACE_CHARGED",
                                "FURNACE / FURNACE_1", "f_temp", "맥락 수준 1",
                                "p01=1000, p99=1300"),
                        new MonitorDashboardMetadata.DefinitionView(
                                "품질 위험구간", "quality-rule", "HEAT_COMPLETE",
                                "FURNACE / FURNACE_1", "[f_temp]", "HEATING / DANGER",
                                "{\"allOf\":[]}")));
        RecordingReplayControl control = new RecordingReplayControl();
        return new Fixture(onEdt(() -> new MonitorDashboardPanel(metadata, control, scheduler)), control);
    }

    private static MonitorUpdate update(boolean ordered, List<HistoricalAlert> alerts) {
        ReplayEvent first = event("evt-1", "mat-1", "FURNACE_1");
        ReplayEvent second = event("evt-2", "mat-2", "FURNACE_1");
        ReplayUnit unit = new ReplayUnit(
                "batch-1", "FURNACE_CHARGED", "2025-02-03", 14,
                "HOUR", "HOUR", List.of(first, second), ordered);
        MaterialSnapshot material1 = material("mat-1", RiskGrade.NORMAL);
        MaterialSnapshot material2 = material("mat-2", RiskGrade.DANGER);
        EquipmentKey key = new EquipmentKey("FURNACE", "FURNACE_1");
        EquipmentSnapshot equipment = new EquipmentSnapshot(
                key, "FURNACE_CHARGED", "2025-02-03", 14, "HOUR",
                List.of("mat-1", "mat-2"),
                Map.of("mat-1", Map.of("f_temp", RiskScalar.of(1120)),
                        "mat-2", Map.of("f_temp", RiskScalar.of(1270))),
                false);
        return new MonitorUpdate(unit, 8, 15,
                Map.of("mat-1", material1, "mat-2", material2),
                Map.of(key, equipment), alerts);
    }

    private static MonitorUpdate overviewRiskUpdate() {
        List<ReplayEvent> events = List.of(
                event("evt-normal", "mat-normal", "FURNACE_1"),
                event("evt-missing", "mat-missing", "FURNACE_1"),
                event("evt-caution", "mat-caution", "FURNACE_1"),
                event("evt-danger", "mat-danger", "FURNACE_1"));
        ReplayUnit unit = new ReplayUnit(
                "batch-1", "FURNACE_CHARGED", "2025-02-03", 14,
                "HOUR", "HOUR", events, false);
        Map<String, MaterialSnapshot> materials = new java.util.LinkedHashMap<>();
        materials.put("mat-normal", material("mat-normal", List.of(range(RangeStatus.TYPICAL)),
                List.of(), RiskGrade.NORMAL, RiskGrade.INSUFFICIENT_EVIDENCE));
        materials.put("mat-missing", material("mat-missing", List.of(range(RangeStatus.DATA_MISSING)),
                List.of(), RiskGrade.INSUFFICIENT_EVIDENCE, RiskGrade.INSUFFICIENT_EVIDENCE));
        materials.put("mat-caution", material("mat-caution", List.of(
                        range(RangeStatus.DATA_MISSING), range(RangeStatus.CAUTION)),
                List.of(), RiskGrade.CAUTION, RiskGrade.NORMAL));
        materials.put("mat-danger", material("mat-danger", List.of(
                        range(RangeStatus.DATA_MISSING), range(RangeStatus.CAUTION),
                        range(RangeStatus.SEVERE)),
                List.of(), RiskGrade.DANGER, RiskGrade.CAUTION));
        return new MonitorUpdate(unit, 1, 4, materials, Map.of(), List.of());
    }

    private static MonitorUpdate sequencedUpdate(int sequence, List<HistoricalAlert> alerts) {
        return sequencedUpdate(sequence, RiskGrade.NORMAL, alerts);
    }

    private static MonitorUpdate sequencedUpdate(int sequence, RiskGrade grade) {
        return sequencedUpdate(sequence, grade, List.of());
    }

    private static MonitorUpdate sequencedUpdate(
            int sequence,
            RiskGrade grade,
            List<HistoricalAlert> alerts) {
        String materialKey = "mat-" + String.format("%05d", sequence);
        ReplayEvent event = event("evt-" + sequence, materialKey, "FURNACE_1");
        ReplayUnit unit = new ReplayUnit(
                "batch-1", "FURNACE_CHARGED", "2025-02-03", 14,
                "HOUR", "HOUR", List.of(event), false);
        return new MonitorUpdate(
                unit, sequence, sequence,
                Map.of(materialKey, material(materialKey, grade)), Map.of(), alerts);
    }

    private static MonitorUpdate evidenceUpdate() {
        return evidenceUpdateWithAlerts(List.of());
    }

    private static MonitorUpdate evidenceUpdateWithAlerts(List<HistoricalAlert> alerts) {
        MetricEvidence discovery = new MetricEvidence(
                100, 20, .20, .12, .28, .18, .08, .10,
                2.25, 1.5, 3.1, .01, .02, ReasonCode.NONE);
        MetricEvidence confirmation = new MetricEvidence(
                40, 3, .075, .025, .195, null, null, null,
                null, null, null, null, null, ReasonCode.LOW_SUPPORT);
        RuleEvaluation rule = new RuleEvaluation(
                "sha256:" + "f".repeat(64), EvidenceFamily.HEATING, RuleMatchStatus.MATCHED,
                true, RiskGrade.DANGER, true, false, discovery, confirmation);
        ReplayEvent event = event("evt-evidence", "mat-evidence", "FURNACE_1");
        ReplayUnit unit = new ReplayUnit(
                "batch-1", "FURNACE_CHARGED", "2025-02-03", 14,
                "HOUR", "HOUR", List.of(event), false);
        MaterialSnapshot material = material(
                "mat-evidence",
                List.of(new RangeEvaluation(
                        "f_temp", FieldRole.DIRECT_OPERATION, RangeStatus.SEVERE, RiskScalar.of(1270),
                        "sha256:" + "r".repeat(64), 2, RangeSelectionReason.EXACT_CONTEXT, true)),
                List.of(rule), RiskGrade.DANGER, RiskGrade.INSUFFICIENT_EVIDENCE);
        return new MonitorUpdate(unit, 1, 1, Map.of("mat-evidence", material), Map.of(), alerts);
    }

    private static MonitorUpdate fiveMaterialEquipmentUpdate() {
        List<ReplayEvent> events = new ArrayList<>();
        Map<String, MaterialSnapshot> materials = new java.util.LinkedHashMap<>();
        Map<String, Map<String, RiskScalar>> values = new java.util.LinkedHashMap<>();
        List<String> materialKeys = new ArrayList<>();
        for (int index = 1; index <= 5; index++) {
            String key = "mat-eq-" + index;
            materialKeys.add(key);
            events.add(event("evt-eq-" + index, key, "FURNACE_1"));
            RangeStatus rangeStatus = index == 5 ? RangeStatus.SEVERE : RangeStatus.TYPICAL;
            RiskGrade quality = index == 5 ? RiskGrade.DANGER : RiskGrade.NORMAL;
            RiskGrade historical = index == 5 ? RiskGrade.CAUTION : RiskGrade.INSUFFICIENT_EVIDENCE;
            materials.put(key, material(
                    key, List.of(range(rangeStatus)), List.of(), quality, historical));
            values.put(key, Map.of("f_temp", RiskScalar.of(1200 + index)));
        }
        ReplayUnit unit = new ReplayUnit(
                "batch-1", "FURNACE_CHARGED", "2025-02-03", 14,
                "HOUR", "HOUR", events, false);
        EquipmentKey equipmentKey = new EquipmentKey("FURNACE", "FURNACE_1");
        EquipmentSnapshot equipment = new EquipmentSnapshot(
                equipmentKey, "FURNACE_CHARGED", "2025-02-03", 14, "HOUR",
                materialKeys, values, false);
        return new MonitorUpdate(unit, 1, 5, materials, Map.of(equipmentKey, equipment), List.of());
    }

    private static MonitorUpdate rm4EquipmentUpdate() {
        ObjectNode values = JsonNodeFactory.instance.objectNode().put("rm4_temp", 1050);
        ReplayEvent event = new ReplayEvent(
                "1.0", BUNDLE, CRITERIA, "evt-rm4", "2025-02-03", 15,
                "HOUR", "batch-rm4", "equipment-batch-rm4", "RM4_RECORDED", "HOUR",
                "mat-rm4", "RM4", "RM4", "charge-1", "slab-1", null, null, values);
        ReplayUnit unit = new ReplayUnit(
                "batch-rm4", "RM4_RECORDED", "2025-02-03", 15,
                "HOUR", "HOUR", List.of(event), false);
        MaterialSnapshot material = new MaterialSnapshot(
                "mat-rm4", "charge-1", "slab-1", null, null,
                "RM4_RECORDED", "RM4", "RM4", "2025-02-03", 15, "HOUR",
                Map.of("rm4_temp", RiskScalar.of(1050)), List.of(range(RangeStatus.TYPICAL)),
                List.of(), List.of(), RiskGrade.NORMAL, RiskGrade.INSUFFICIENT_EVIDENCE, 0);
        EquipmentKey key = new EquipmentKey("RM4", "RM4");
        EquipmentSnapshot equipment = new EquipmentSnapshot(
                key, "RM4_RECORDED", "2025-02-03", 15, "HOUR",
                List.of("mat-rm4"), Map.of("mat-rm4", Map.of("rm4_temp", RiskScalar.of(1050))), false);
        return new MonitorUpdate(
                unit, 2, 3, Map.of("mat-rm4", material), Map.of(key, equipment), List.of());
    }

    private static ReplayEvent event(String eventId, String material, String equipmentId) {
        ObjectNode values = JsonNodeFactory.instance.objectNode().put("f_temp", 1250);
        return new ReplayEvent(
                "1.0", BUNDLE, CRITERIA, eventId, "2025-02-03", 14,
                "HOUR", "batch-1", "equipment-batch-1", "FURNACE_CHARGED", "HOUR",
                material, "FURNACE", equipmentId, "charge-1", "slab-1", null, null, values);
    }

    private static MaterialSnapshot material(String material, RiskGrade grade) {
        return material(material, List.of(), List.of(), grade, RiskGrade.INSUFFICIENT_EVIDENCE);
    }

    private static MaterialSnapshot material(
            String material,
            List<RangeEvaluation> ranges,
            List<RuleEvaluation> rules,
            RiskGrade qualityRisk,
            RiskGrade historicalRisk) {
        return new MaterialSnapshot(
                material, "charge-1", "slab-1", null, null,
                "FURNACE_CHARGED", "FURNACE", "FURNACE_1", "2025-02-03", 14, "HOUR",
                Map.of("f_temp", RiskScalar.of(1250)), ranges, rules, List.of(),
                qualityRisk, historicalRisk, rules.stream().filter(RuleEvaluation::matched).count());
    }

    private static RangeEvaluation range(RangeStatus status) {
        return new RangeEvaluation(
                "f_temp", FieldRole.DIRECT_OPERATION, status, RiskScalar.of(1250),
                "range-rule", 1, RangeSelectionReason.EXACT_CONTEXT,
                status == RangeStatus.CAUTION || status == RangeStatus.SEVERE);
    }

    private static HistoricalAlert alert() {
        return alert("2025-02-03", "mat-2", "FURNACE", "FURNACE_1", RiskGrade.DANGER, "c");
    }

    private static HistoricalAlert alert(
            String replayDate,
            String material,
            String equipmentType,
            String equipmentId,
            RiskGrade grade,
            String ruleSeed) {
        MetricEvidence evidence = new MetricEvidence(
                100, 20, .2, .12, .28, .18, .08, .10, 2.25, 1.5, 3.1, .01, .02,
                ReasonCode.NONE);
        return new HistoricalAlert(
                new AlertKey(BUNDLE, material, "FURNACE_CHARGED", "sha256:" + ruleSeed.repeat(64)),
                AlertKind.QUALITY_RISK, "evt-" + material, replayDate, 14,
                equipmentType, equipmentId, "f_temp", RiskScalar.of(1270),
                RangeStatus.SEVERE, grade, false, evidence, evidence);
    }

    private static HistoricalAlert largeAlert(int index) {
        String material = "mat-" + String.format("%05d", index);
        MetricEvidence evidence = new MetricEvidence(
                100, 20, .2, .12, .28, .18, .08, .10,
                2.25, 1.5, 3.1, .01, .02, ReasonCode.NONE);
        return new HistoricalAlert(
                new AlertKey(BUNDLE, material, "FURNACE_CHARGED",
                        "sha256:" + String.format("%064x", index)),
                AlertKind.QUALITY_RISK, "evt-" + index, "2025-02-03", 14,
                "FURNACE", "FURNACE_1", "f_temp", RiskScalar.of(1270),
                RangeStatus.SEVERE, RiskGrade.DANGER, index % 5 == 0, evidence, evidence);
    }

    private static HistoricalAlert operatingAlert(int index) {
        String material = "mat-" + String.format("%05d", index);
        return new HistoricalAlert(
                new AlertKey(BUNDLE, material, "FURNACE_CHARGED",
                        "sha256:" + String.format("%064x", index + 100_000)),
                AlertKind.OPERATING_RANGE, "evt-" + index, "2025-02-03", 14,
                "FURNACE", "FURNACE_1", "f_temp", RiskScalar.of(1310),
                RangeStatus.SEVERE, RiskGrade.DANGER, false, null, null);
    }

    private static void assertOverview(
            JTable table,
            String material,
            String range,
            String earlyQuality,
            String apHistory) {
        int row = rowOf(table, "소재", material);
        assertThat(table.getValueAt(row, columnOf(table, "설비 운전범위"))).isEqualTo(range);
        assertThat(table.getValueAt(row, columnOf(table, "조기 품질 위험"))).isEqualTo(earlyQuality);
        assertThat(table.getValueAt(row, columnOf(table, "AP 후행 품질 확인"))).isEqualTo(apHistory);
    }

    private static List<String> columnNames(JTable table) {
        List<String> names = new ArrayList<>();
        for (int column = 0; column < table.getColumnCount(); column++) {
            names.add(table.getColumnName(column));
        }
        return names;
    }

    private static int rowOf(JTable table, String columnName, String expected) {
        int column = columnOf(table, columnName);
        for (int row = 0; row < table.getRowCount(); row++) {
            if (expected.equals(String.valueOf(table.getValueAt(row, column)))) return row;
        }
        throw new AssertionError("row not found: " + columnName + "=" + expected);
    }

    private static int columnOf(JTable table, String name) {
        for (int column = 0; column < table.getColumnCount(); column++) {
            if (name.equals(table.getColumnName(column))) return column;
        }
        throw new AssertionError("column not found: " + name);
    }

    private static String tableRowText(JTable table, int row) {
        List<String> values = new ArrayList<>();
        for (int column = 0; column < table.getColumnCount(); column++) {
            values.add(String.valueOf(table.getValueAt(row, column)));
        }
        return String.join(" | ", values);
    }

    private static String tableText(JTable table) {
        List<String> rows = new ArrayList<>();
        for (int row = 0; row < table.getRowCount(); row++) rows.add(tableRowText(table, row));
        return String.join("\n", rows);
    }

    private static List<String> comboItems(JComboBox<?> combo) {
        List<String> values = new ArrayList<>();
        for (int index = 0; index < combo.getItemCount(); index++) {
            values.add(String.valueOf(combo.getItemAt(index)));
        }
        return values;
    }

    private static List<String> tabTitles(JTabbedPane tabs) {
        List<String> titles = new ArrayList<>();
        for (int i = 0; i < tabs.getTabCount(); i++) {
            titles.add(tabs.getTitleAt(i));
        }
        return titles;
    }

    private static void click(Container root, String text) {
        componentWithText(root, text, AbstractButton.class).doClick();
    }

    private static String visibleText(Container root) {
        List<String> parts = new ArrayList<>();
        collectVisibleText(root, parts);
        return String.join("\n", parts);
    }

    private static void collectVisibleText(Component component, List<String> parts) {
        if (component instanceof JLabel label) {
            parts.add(label.getText());
        } else if (component instanceof AbstractButton button) {
            parts.add(button.getText());
        } else if (component instanceof JTable table) {
            for (int row = 0; row < table.getRowCount(); row++) {
                for (int column = 0; column < table.getColumnCount(); column++) {
                    parts.add(String.valueOf(table.getValueAt(row, column)));
                }
            }
        }
        if (component instanceof Container container) {
            for (Component child : container.getComponents()) {
                collectVisibleText(child, parts);
            }
        }
    }

    private static <T extends Component> T component(Container root, String name, Class<T> type) {
        if (name.equals(root.getName()) && type.isInstance(root)) {
            return type.cast(root);
        }
        for (Component child : root.getComponents()) {
            if (name.equals(child.getName()) && type.isInstance(child)) {
                return type.cast(child);
            }
            if (child instanceof Container container) {
                T found = componentOrNull(container, name, type);
                if (found != null) return found;
            }
        }
        throw new AssertionError("component not found: " + name);
    }

    private static <T extends Component> T componentOrNull(Container root, String name, Class<T> type) {
        if (name.equals(root.getName()) && type.isInstance(root)) return type.cast(root);
        for (Component child : root.getComponents()) {
            if (name.equals(child.getName()) && type.isInstance(child)) return type.cast(child);
            if (child instanceof Container container) {
                T found = componentOrNull(container, name, type);
                if (found != null) return found;
            }
        }
        return null;
    }

    private static <T extends Component> T componentWithText(Container root, String text, Class<T> type) {
        for (Component child : root.getComponents()) {
            if (type.isInstance(child) && child instanceof AbstractButton button && text.equals(button.getText())) {
                return type.cast(child);
            }
            if (child instanceof Container container) {
                T found = componentWithTextOrNull(container, text, type);
                if (found != null) return found;
            }
        }
        throw new AssertionError("component not found: " + text);
    }

    private static <T extends Component> T componentWithTextOrNull(Container root, String text, Class<T> type) {
        for (Component child : root.getComponents()) {
            if (type.isInstance(child) && child instanceof AbstractButton button && text.equals(button.getText())) {
                return type.cast(child);
            }
            if (child instanceof Container container) {
                T found = componentWithTextOrNull(container, text, type);
                if (found != null) return found;
            }
        }
        return null;
    }

    private static void onEdt(ThrowingRunnable action) throws Exception {
        onEdt(() -> {
            action.run();
            return null;
        });
    }

    private static <T> T onEdt(ThrowingSupplier<T> action) throws Exception {
        if (SwingUtilities.isEventDispatchThread()) return action.get();
        AtomicReference<T> value = new AtomicReference<>();
        AtomicReference<Throwable> failure = new AtomicReference<>();
        SwingUtilities.invokeAndWait(() -> {
            try {
                value.set(action.get());
            } catch (Throwable error) {
                failure.set(error);
            }
        });
        if (failure.get() instanceof Exception exception) throw exception;
        if (failure.get() != null) throw new AssertionError(failure.get());
        return value.get();
    }

    private record Fixture(MonitorDashboardPanel panel, RecordingReplayControl control) { }

    private static final class RecordingReplayControl implements ReplayControl {
        private final List<String> actions = new ArrayList<>();
        private final AtomicReference<ReplayControllerState> state = new AtomicReference<>(
                new ReplayControllerState(ReplayStatus.STOPPED, true, ReplaySpeed.X1, 0, null, null));
        private boolean failNextStateRead;

        @Override public void start() { actions.add("start"); }
        @Override public void pause() { actions.add("pause"); }
        @Override public void resume() { actions.add("resume"); }
        @Override public void step() { actions.add("step"); }
        @Override public void stop() { actions.add("stop"); }
        @Override public void advanceToNextDate() { actions.add("next-date"); }
        @Override public void setSpeed(ReplaySpeed speed) { actions.add("speed:" + speed); }
        @Override public void reportUiFailure(ReplayUnit unit, Throwable error) {
            actions.add("ui-failure:" + unit.batchId());
            state.set(new ReplayControllerState(
                    ReplayStatus.ERROR, true, ReplaySpeed.X1,
                    state.get().unitsDelivered(), unit.replayDate(),
                    ReplayFailureContext.from(unit),
                    List.of(ReplayStatus.STOPPED, ReplayStatus.ERROR),
                    error.getMessage()));
        }
        @Override public ReplayControllerState state() {
            if (failNextStateRead) {
                failNextStateRead = false;
                throw new IllegalStateException("render exploded");
            }
            return state.get();
        }
    }

    private static final class RecordingUpdateScheduler implements MonitorDashboardPanel.UpdateScheduler {
        private final List<Runnable> pending = new ArrayList<>();

        @Override
        public void schedule(Runnable operation) {
            pending.add(operation);
        }

        int pendingCount() {
            return pending.size();
        }

        void runNext() {
            pending.removeFirst().run();
        }
    }

    @FunctionalInterface
    private interface ThrowingRunnable { void run() throws Exception; }

    @FunctionalInterface
    private interface ThrowingSupplier<T> { T get() throws Exception; }
}
