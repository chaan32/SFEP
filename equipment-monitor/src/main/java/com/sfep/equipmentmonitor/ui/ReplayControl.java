package com.sfep.equipmentmonitor.ui;

import com.sfep.equipmentmonitor.replay.ReplayControllerState;
import com.sfep.equipmentmonitor.replay.ReplaySpeed;
import com.sfep.equipmentmonitor.replay.ReplayUnit;

/** UI-facing replay operations; implementations own replay-thread coordination. */
public interface ReplayControl {
    void start();

    void pause();

    void resume();

    void step();

    void stop();

    void advanceToNextDate();

    void setSpeed(ReplaySpeed speed);

    void reportUiFailure(ReplayUnit unit, Throwable error);

    ReplayControllerState state();
}
