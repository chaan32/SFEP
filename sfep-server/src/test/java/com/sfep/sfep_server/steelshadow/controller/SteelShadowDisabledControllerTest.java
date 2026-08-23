package com.sfep.sfep_server.steelshadow.controller;

import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.autoconfigure.web.servlet.AutoConfigureMockMvc;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.test.web.servlet.MockMvc;

import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

@SpringBootTest(properties = "sfep.steel-shadow.enabled=false")
@AutoConfigureMockMvc
class SteelShadowDisabledControllerTest {

    @Autowired
    MockMvc mvc;

    @Test
    void disabledAdapterDoesNotExposeEndpoints() throws Exception {
        mvc.perform(get("/api/steel-shadow/status"))
                .andExpect(status().isNotFound());
    }
}
