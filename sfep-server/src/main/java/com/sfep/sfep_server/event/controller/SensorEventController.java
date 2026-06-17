package com.sfep.sfep_server.event.controller;

import com.sfep.sfep_server.event.dto.BatchSensorEventRequest;
import com.sfep.sfep_server.event.dto.DirectWriteResult;
import com.sfep.sfep_server.event.dto.SensorEventRequest;
import com.sfep.sfep_server.event.dto.SensorEventResponse;
import com.sfep.sfep_server.event.service.SensorEventService;
import jakarta.validation.Valid;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

@RestController
@RequestMapping("/api/v1/events")
public class SensorEventController {

    private final SensorEventService sensorEventService;

    public SensorEventController(SensorEventService sensorEventService) {
        this.sensorEventService = sensorEventService;
    }

    @PostMapping("/direct")
    public SensorEventResponse collectDirect(@Valid @RequestBody SensorEventRequest request) {
        return sensorEventService.saveDirect(request);
    }

    @PostMapping("/direct/batch")
    public DirectWriteResult collectDirectBatch(@Valid @RequestBody BatchSensorEventRequest request) {
        return sensorEventService.saveDirectBatch(request.events());
    }
}
