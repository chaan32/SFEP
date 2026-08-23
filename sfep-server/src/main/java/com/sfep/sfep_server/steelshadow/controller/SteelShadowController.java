package com.sfep.sfep_server.steelshadow.controller;

import com.sfep.sfep_server.steelshadow.dto.SteelShadowResponses.SteelShadowLabelResponse;
import com.sfep.sfep_server.steelshadow.dto.SteelShadowResponses.SteelShadowPredictionResponse;
import com.sfep.sfep_server.steelshadow.dto.SteelShadowResponses.SteelShadowScoreResponse;
import com.sfep.sfep_server.steelshadow.dto.SteelShadowResponses.SteelShadowStatusResponse;
import com.sfep.sfep_server.steelshadow.service.SteelShadowRequestParser;
import com.sfep.sfep_server.steelshadow.service.SteelShadowService;
import jakarta.validation.constraints.NotBlank;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.validation.annotation.Validated;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

@Validated
@RestController
@RequestMapping("/api/steel-shadow")
@ConditionalOnProperty(prefix = "sfep.steel-shadow", name = "enabled", havingValue = "true")
public class SteelShadowController {
    private final SteelShadowService service;
    private final SteelShadowRequestParser requestParser;

    public SteelShadowController(
            SteelShadowService service,
            SteelShadowRequestParser requestParser
    ) {
        this.service = service;
        this.requestParser = requestParser;
    }

    @PostMapping("/features")
    public SteelShadowScoreResponse score(
            @RequestBody byte[] body
    ) {
        return service.score(requestParser.parseFeatures(body));
    }

    @PostMapping("/labels")
    public SteelShadowLabelResponse ingestLabels(
            @RequestBody byte[] body
    ) {
        return service.ingestLabels(requestParser.parseLabels(body));
    }

    @GetMapping("/status")
    public SteelShadowStatusResponse status() {
        return service.status();
    }

    @GetMapping("/predictions/{hrCoilId}")
    public SteelShadowPredictionResponse predictions(
            @PathVariable @NotBlank String hrCoilId
    ) {
        return service.predictions(hrCoilId);
    }
}
