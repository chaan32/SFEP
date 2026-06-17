package com.sfep.sfep_server.simulator.controller;

import com.sfep.sfep_server.simulator.dto.SimulatorRunRequest;
import com.sfep.sfep_server.simulator.dto.SimulatorRunResponse;
import com.sfep.sfep_server.simulator.service.FactorySimulatorService;
import jakarta.validation.Valid;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

@RestController
@RequestMapping("/api/v1/simulator")
public class FactorySimulatorController {

    private final FactorySimulatorService factorySimulatorService;

    public FactorySimulatorController(FactorySimulatorService factorySimulatorService) {
        this.factorySimulatorService = factorySimulatorService;
    }

    @PostMapping("/direct/run")
    public SimulatorRunResponse runDirectWrite(@Valid @RequestBody SimulatorRunRequest request) {
        return factorySimulatorService.runDirectWrite(request);
    }
}
