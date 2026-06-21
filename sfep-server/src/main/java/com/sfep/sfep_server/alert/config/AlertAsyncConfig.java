package com.sfep.sfep_server.alert.config;

import org.springframework.beans.factory.annotation.Value;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.scheduling.concurrent.ThreadPoolTaskExecutor;

import java.util.concurrent.Executor;
import java.util.concurrent.ThreadPoolExecutor;

@Configuration
public class AlertAsyncConfig {

    // Alert Thread.
    // It will execute on sfep-alert-
    @Bean(name = "alertEventExecutor")
    public Executor alertEventExecutor(
            @Value("${sfep.alert.executor.core-pool-size:2}") int corePoolSize,
            @Value("${sfep.alert.executor.max-pool-size:4}") int maxPoolSize,
            @Value("${sfep.alert.executor.queue-capacity:10000}") int queueCapacity
    ) {
        // Make new Thread, and Execute the Alerting
        ThreadPoolTaskExecutor executor = new ThreadPoolTaskExecutor();
        executor.setCorePoolSize(corePoolSize);
        executor.setMaxPoolSize(maxPoolSize);
        executor.setQueueCapacity(queueCapacity);
        executor.setThreadNamePrefix("sfep-alert-");
        // when the queue is FULL, Drop the past Alert And save recent Alert
        executor.setRejectedExecutionHandler(new ThreadPoolExecutor.DiscardOldestPolicy());
        executor.initialize();
        return executor;
    }
}
