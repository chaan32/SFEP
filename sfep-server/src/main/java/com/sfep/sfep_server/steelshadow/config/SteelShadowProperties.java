package com.sfep.sfep_server.steelshadow.config;

import org.springframework.boot.context.properties.ConfigurationProperties;

import java.net.URI;
import java.time.Duration;
import java.util.Locale;
import java.util.Objects;
import java.util.Set;

@ConfigurationProperties("sfep.steel-shadow")
public record SteelShadowProperties(
        boolean enabled,
        URI baseUrl,
        Duration connectTimeout,
        Duration readTimeout
) {
    private static final Set<String> LOOPBACK_HOSTS = Set.of("127.0.0.1", "localhost", "::1");

    public SteelShadowProperties {
        Objects.requireNonNull(baseUrl, "baseUrl must not be null");
        Objects.requireNonNull(connectTimeout, "connectTimeout must not be null");
        Objects.requireNonNull(readTimeout, "readTimeout must not be null");
        String scheme = baseUrl.getScheme();
        String host = baseUrl.getHost();
        if (!baseUrl.isAbsolute()
                || scheme == null
                || !scheme.equalsIgnoreCase("http")
                || host == null
                || !LOOPBACK_HOSTS.contains(host.toLowerCase(Locale.ROOT))
                || baseUrl.getUserInfo() != null
                || baseUrl.getQuery() != null
                || baseUrl.getFragment() != null) {
            throw new IllegalArgumentException("Steel Shadow baseUrl must be an HTTP loopback URL");
        }
        if (connectTimeout.isZero() || connectTimeout.isNegative()) {
            throw new IllegalArgumentException("Steel Shadow connectTimeout must be positive");
        }
        if (readTimeout.isZero() || readTimeout.isNegative()) {
            throw new IllegalArgumentException("Steel Shadow readTimeout must be positive");
        }
    }
}
