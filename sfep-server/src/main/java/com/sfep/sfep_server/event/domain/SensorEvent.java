package com.sfep.sfep_server.event.domain;

import com.sfep.sfep_server.equipment.domain.EquipmentStatus;
import com.sfep.sfep_server.equipment.domain.EquipmentType;
import jakarta.persistence.Column;
import jakarta.persistence.Entity;
import jakarta.persistence.EnumType;
import jakarta.persistence.Enumerated;
import jakarta.persistence.GeneratedValue;
import jakarta.persistence.GenerationType;
import jakarta.persistence.Id;
import jakarta.persistence.Index;
import jakarta.persistence.Table;
import jakarta.persistence.UniqueConstraint;

import java.time.Instant;

@Entity
@Table(
        name = "sensor_event",
        indexes = {
                @Index(name = "idx_sensor_event_equipment_time", columnList = "equipment_id, occurred_at"),
                @Index(name = "idx_sensor_event_severity_time", columnList = "severity, occurred_at"),
                @Index(name = "idx_sensor_event_status_time", columnList = "status, occurred_at")
        },
        uniqueConstraints = {
                @UniqueConstraint(name = "uk_sensor_event_event_id", columnNames = "event_id")
        }
)
public class SensorEvent {

    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;

    @Column(name = "event_id", nullable = false, length = 64)
    private String eventId;

    @Column(name = "equipment_id", nullable = false, length = 64)
    private String equipmentId;

    @Enumerated(EnumType.STRING)
    @Column(name = "equipment_type", nullable = false, length = 32)
    private EquipmentType equipmentType;

    @Enumerated(EnumType.STRING)
    @Column(nullable = false, length = 32)
    private EquipmentStatus status;

    @Enumerated(EnumType.STRING)
    @Column(nullable = false, length = 32)
    private EventSeverity severity;

    @Column(nullable = false)
    private double temperature;

    @Column(nullable = false)
    private double vibration;

    @Column(nullable = false)
    private double rpm;

    @Column(nullable = false)
    private double power;

    @Column(name = "current_value", nullable = false)
    private double currentValue;

    @Column(nullable = false)
    private boolean anomaly;

    @Column(name = "occurred_at", nullable = false)
    private Instant occurredAt;

    @Column(name = "received_at", nullable = false)
    private Instant receivedAt;

    protected SensorEvent() {
    }

    public SensorEvent(
            String eventId,
            String equipmentId,
            EquipmentType equipmentType,
            EquipmentStatus status,
            EventSeverity severity,
            double temperature,
            double vibration,
            double rpm,
            double power,
            double currentValue,
            boolean anomaly,
            Instant occurredAt,
            Instant receivedAt
    ) {
        this.eventId = eventId;
        this.equipmentId = equipmentId;
        this.equipmentType = equipmentType;
        this.status = status;
        this.severity = severity;
        this.temperature = temperature;
        this.vibration = vibration;
        this.rpm = rpm;
        this.power = power;
        this.currentValue = currentValue;
        this.anomaly = anomaly;
        this.occurredAt = occurredAt;
        this.receivedAt = receivedAt;
    }

    public Long getId() {
        return id;
    }

    public String getEventId() {
        return eventId;
    }

    public String getEquipmentId() {
        return equipmentId;
    }

    public EquipmentType getEquipmentType() {
        return equipmentType;
    }

    public EquipmentStatus getStatus() {
        return status;
    }

    public EventSeverity getSeverity() {
        return severity;
    }

    public double getTemperature() {
        return temperature;
    }

    public double getVibration() {
        return vibration;
    }

    public double getRpm() {
        return rpm;
    }

    public double getPower() {
        return power;
    }

    public double getCurrentValue() {
        return currentValue;
    }

    public boolean isAnomaly() {
        return anomaly;
    }

    public Instant getOccurredAt() {
        return occurredAt;
    }

    public Instant getReceivedAt() {
        return receivedAt;
    }
}
