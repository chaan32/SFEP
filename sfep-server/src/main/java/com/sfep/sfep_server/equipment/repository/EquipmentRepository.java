package com.sfep.sfep_server.equipment.repository;

import com.sfep.sfep_server.equipment.domain.Equipment;
import com.sfep.sfep_server.equipment.domain.EquipmentStatus;
import org.springframework.data.jpa.repository.JpaRepository;

public interface EquipmentRepository extends JpaRepository<Equipment, String> {

    long countByStatus(EquipmentStatus status);
}
