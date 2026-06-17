package com.sfep.sfep_server.event.repository;

import com.sfep.sfep_server.event.domain.EventSeverity;
import com.sfep.sfep_server.event.domain.SensorEvent;
import org.springframework.data.jpa.repository.JpaRepository;

import java.time.Instant;
import java.util.List;

public interface SensorEventRepository extends JpaRepository<SensorEvent, Long> {

    long countByOccurredAtAfter(Instant occurredAt);

    long countBySeverity(EventSeverity severity);

    List<SensorEvent> findTop20ByOrderByOccurredAtDesc();
}
