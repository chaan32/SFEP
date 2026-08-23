package com.sfep.sfep_server.steelshadow.config;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.sfep.sfep_server.steelshadow.client.SteelShadowClient;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.boot.context.properties.EnableConfigurationProperties;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.http.client.JdkClientHttpRequestFactory;
import org.springframework.web.client.RestClient;

import java.net.http.HttpClient;

@Configuration
@EnableConfigurationProperties(SteelShadowProperties.class)
public class SteelShadowClientConfig {

    @Bean
    @ConditionalOnProperty(prefix = "sfep.steel-shadow", name = "enabled", havingValue = "true")
    public RestClient steelShadowRestClient(
            RestClient.Builder builder,
            SteelShadowProperties properties
    ) {
        HttpClient httpClient = HttpClient.newBuilder()
                .connectTimeout(properties.connectTimeout())
                .build();
        JdkClientHttpRequestFactory requestFactory = new JdkClientHttpRequestFactory(httpClient);
        requestFactory.setReadTimeout(properties.readTimeout());
        return builder
                .baseUrl(properties.baseUrl().toString())
                .requestFactory(requestFactory)
                .build();
    }

    @Bean
    @ConditionalOnProperty(prefix = "sfep.steel-shadow", name = "enabled", havingValue = "true")
    public SteelShadowClient steelShadowClient(
            RestClient steelShadowRestClient,
            ObjectMapper objectMapper
    ) {
        return new SteelShadowClient(steelShadowRestClient, objectMapper);
    }
}
