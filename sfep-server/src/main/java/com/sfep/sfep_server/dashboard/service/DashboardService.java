package com.sfep.sfep_server.dashboard.service;

import com.sfep.sfep_server.dashboard.dto.DashboardSummaryResponse;
import com.sfep.sfep_server.equipment.domain.EquipmentStatus;
import com.sfep.sfep_server.equipment.repository.EquipmentRepository;
import com.sfep.sfep_server.event.domain.EventSeverity;
import com.sfep.sfep_server.event.dto.SensorEventResponse;
import com.sfep.sfep_server.event.repository.SensorEventRepository;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.time.Instant;
import java.util.List;

@Service
public class DashboardService {

    private final EquipmentRepository equipmentRepository;
    private final SensorEventRepository sensorEventRepository;

    public DashboardService(
            EquipmentRepository equipmentRepository,
            SensorEventRepository sensorEventRepository
    ) {
        this.equipmentRepository = equipmentRepository;
        this.sensorEventRepository = sensorEventRepository;
    }

    @Transactional(readOnly = true)
    public DashboardSummaryResponse getSummary() {
        List<SensorEventResponse> recentEvents = sensorEventRepository.findTop20ByOrderByOccurredAtDesc()
                .stream()
                .map(SensorEventResponse::from)
                .toList();

        return new DashboardSummaryResponse(
                equipmentRepository.count(),
                equipmentRepository.countByStatus(EquipmentStatus.RUNNING),
                equipmentRepository.countByStatus(EquipmentStatus.IDLE),
                equipmentRepository.countByStatus(EquipmentStatus.WARNING),
                equipmentRepository.countByStatus(EquipmentStatus.FAILURE),
                sensorEventRepository.count(),
                sensorEventRepository.countBySeverity(EventSeverity.WARNING),
                sensorEventRepository.countBySeverity(EventSeverity.CRITICAL),
                sensorEventRepository.countByOccurredAtAfter(Instant.now().minusSeconds(60)),
                recentEvents
        );
    }
}
