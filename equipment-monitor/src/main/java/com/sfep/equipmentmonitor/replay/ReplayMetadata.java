package com.sfep.equipmentmonitor.replay;

public record ReplayMetadata(long rowCount, String firstEventId, String lastEventId) {
}
