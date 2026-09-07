package com.sfep.equipmentmonitor.replay;

import java.util.Optional;

public interface ReplayUnitCursor extends AutoCloseable {
    Optional<ReplayUnit> nextUnit();

    /** True only when the cursor can prove that no unit follows the last returned unit. */
    default boolean exhausted() {
        return false;
    }

    @Override
    void close();
}
