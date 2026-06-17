package com.sfep.sfep_server.simulator.dto;

import com.sfep.sfep_server.event.dto.DirectWriteResult;

public record SimulatorRunResponse(
        int equipmentCount,
        int eventsPerEquipment,
        int generatedEvents,
        int savedEvents,
        long elapsedMs,
        double eventsPerSecond,
        String mode
) {

    public static SimulatorRunResponse of(
            int equipmentCount,
            int eventsPerEquipment,
            DirectWriteResult result,
            String mode
    ) {
        return new SimulatorRunResponse(
                equipmentCount,
                eventsPerEquipment,
                result.requestedEvents(),
                result.savedEvents(),
                result.elapsedMs(),
                result.eventsPerSecond(),
                mode
        );
    }
}
