package com.sfep.sfep_server.equipment.domain;

import jakarta.persistence.Column;
import jakarta.persistence.Entity;
import jakarta.persistence.EnumType;
import jakarta.persistence.Enumerated;
import jakarta.persistence.Id;
import jakarta.persistence.Table;

import java.time.Instant;

@Entity
@Table(name = "equipment")
public class Equipment {

    @Id
    @Column(name = "equipment_id", nullable = false, length = 64)
    private String equipmentId;

    @Enumerated(EnumType.STRING)
    @Column(nullable = false, length = 32)
    private EquipmentType type;

    @Enumerated(EnumType.STRING)
    @Column(nullable = false, length = 32)
    private EquipmentStatus status;

    @Column(nullable = false)
    private Instant lastEventAt;

    @Column(nullable = false)
    private Instant createdAt;

    @Column(nullable = false)
    private Instant updatedAt;

    protected Equipment() {
    }

    public Equipment(String equipmentId, EquipmentType type, EquipmentStatus status, Instant now) {
        this.equipmentId = equipmentId;
        this.type = type;
        this.status = status;
        this.lastEventAt = now;
        this.createdAt = now;
        this.updatedAt = now;
    }

    public void updateLatestStatus(EquipmentType type, EquipmentStatus status, Instant eventAt, Instant now) {
        this.type = type;
        this.status = status;
        this.lastEventAt = eventAt;
        this.updatedAt = now;
    }

    public String getEquipmentId() {
        return equipmentId;
    }

    public EquipmentType getType() {
        return type;
    }

    public EquipmentStatus getStatus() {
        return status;
    }

    public Instant getLastEventAt() {
        return lastEventAt;
    }
}
