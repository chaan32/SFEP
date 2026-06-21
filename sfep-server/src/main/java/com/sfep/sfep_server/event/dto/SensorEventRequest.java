package com.sfep.sfep_server.event.dto;

import com.sfep.sfep_server.equipment.domain.EquipmentStatus;
import com.sfep.sfep_server.equipment.domain.EquipmentType;
import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.NotNull;

import java.time.Instant;

public record SensorEventRequest(
        // 이벤트 고유 ID
        @NotBlank String eventId,
        // 설비 ID
        @NotBlank String equipmentId,
        // 설비 타입
        @NotNull EquipmentType equipmentType,
        // RUNNING, IDLE, WARNING, FAILURE (상태)
        @NotNull EquipmentStatus status,
        // 온도
        @NotNull Double temperature,
        // 진동
        @NotNull Double vibration,
        // 회전 수
        @NotNull Double rpm,
        // 전력 사용량
        @NotNull Double power,
        // 현재 전류
        @NotNull Double currentValue,
        // 이벤트 발생 시간 ( 측정 시간 )
        Instant occurredAt
) {
}
