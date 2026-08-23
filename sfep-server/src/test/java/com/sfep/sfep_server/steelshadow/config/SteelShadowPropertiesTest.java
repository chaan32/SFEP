package com.sfep.sfep_server.steelshadow.config;

import org.junit.jupiter.api.Test;

import java.net.URI;
import java.time.Duration;

import static org.assertj.core.api.Assertions.assertThatThrownBy;

class SteelShadowPropertiesTest {

    @Test
    void rejectsNonLoopbackSidecarBaseUrl() {
        assertThatThrownBy(() -> new SteelShadowProperties(
                true,
                URI.create("https://shadow.example.com"),
                Duration.ofSeconds(2),
                Duration.ofSeconds(120)
        )).isInstanceOf(IllegalArgumentException.class)
                .hasMessageContaining("loopback");
    }
}
