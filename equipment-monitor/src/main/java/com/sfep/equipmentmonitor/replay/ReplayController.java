package com.sfep.equipmentmonitor.replay;

import java.util.ArrayDeque;
import java.util.List;
import java.util.Objects;
import java.util.Optional;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.TimeUnit;

/** Drives a one-pass replay cursor on one background thread. */
public final class ReplayController implements AutoCloseable {
    private static final int MAX_STATUS_HISTORY = 128;

    private enum Request {
        RUN,
        STEP,
        NEXT_DATE,
        PAUSE,
        STOP,
        FAIL,
        CLOSE
    }

    private final Object monitor = new Object();
    private final ReplayUnitCursor cursor;
    private final ReplayUnitConsumer consumer;
    private final ReplayDelay delay;
    private final ExecutorService executor;

    private ReplayStatus status = ReplayStatus.STOPPED;
    private Request request = Request.STOP;
    private boolean paused = true;
    private ReplaySpeed speed = ReplaySpeed.X1;
    private long unitsDelivered;
    private String currentReplayDate;
    private String dateBoundaryOrigin;
    private String errorMessage;
    private ReplayFailureContext failureContext;
    private final ArrayDeque<ReplayStatus> statusHistory =
            new ArrayDeque<>(List.of(ReplayStatus.STOPPED));
    private boolean workerScheduled;
    private boolean closed;
    private ReplayUnit pending;
    private volatile Thread workerThread;

    public ReplayController(ReplayUnitCursor cursor, ReplayUnitConsumer consumer) {
        this(cursor, consumer, ReplayDelay.realTime());
    }

    public ReplayController(
            ReplayUnitCursor cursor,
            ReplayUnitConsumer consumer,
            ReplayDelay delay) {
        this.cursor = Objects.requireNonNull(cursor, "cursor");
        this.consumer = Objects.requireNonNull(consumer, "consumer");
        this.delay = Objects.requireNonNull(delay, "delay");
        this.executor = Executors.newSingleThreadExecutor(task -> {
            Thread thread = new Thread(task, "sfep-replay-controller");
            thread.setDaemon(true);
            workerThread = thread;
            return thread;
        });
    }

    public void start() {
        synchronized (monitor) {
            ensureCanAdvance();
            request = Request.RUN;
            dateBoundaryOrigin = null;
            transitionTo(ReplayStatus.RUNNING);
            paused = false;
            scheduleWorker();
        }
    }

    public void resume() {
        synchronized (monitor) {
            ensureOpen();
            if (status != ReplayStatus.PAUSED && status != ReplayStatus.STOPPED) {
                throw new IllegalStateException("replay is not paused or stopped");
            }
        }
        start();
    }

    public void pause() {
        synchronized (monitor) {
            ensureOpen();
            if (status == ReplayStatus.RUNNING) {
                request = Request.PAUSE;
            }
        }
    }

    public void stop() {
        synchronized (monitor) {
            ensureOpen();
            if (status == ReplayStatus.RUNNING) {
                request = Request.STOP;
            } else if (status == ReplayStatus.PAUSED) {
                request = Request.STOP;
                dateBoundaryOrigin = null;
                transitionTo(ReplayStatus.STOPPED);
                paused = true;
            }
        }
    }

    public void step() {
        synchronized (monitor) {
            ensureCanAdvance();
            if (status == ReplayStatus.RUNNING) {
                throw new IllegalStateException("cannot step while replay is running");
            }
            request = Request.STEP;
            dateBoundaryOrigin = null;
            transitionTo(ReplayStatus.RUNNING);
            paused = false;
            scheduleWorker();
        }
    }

    /**
     * Advances without playback delay to the first unit of the following replay date.
     * Before the first unit has been delivered, this positions the replay at that first unit.
     */
    public void advanceToNextDate() {
        synchronized (monitor) {
            ensureCanAdvance();
            if (status == ReplayStatus.RUNNING) {
                throw new IllegalStateException("cannot move dates while replay is running");
            }
            dateBoundaryOrigin = currentReplayDate;
            request = Request.NEXT_DATE;
            transitionTo(ReplayStatus.RUNNING);
            paused = false;
            scheduleWorker();
        }
    }

    public void setSpeed(ReplaySpeed speed) {
        synchronized (monitor) {
            ensureOpen();
            this.speed = Objects.requireNonNull(speed, "speed");
        }
    }

    public ReplayControllerState state() {
        synchronized (monitor) {
            return new ReplayControllerState(
                    status, paused, speed, unitsDelivered, currentReplayDate,
                    failureContext, List.copyOf(statusHistory), errorMessage);
        }
    }

    /** Stops replay after a UI-side application failure and preserves the affected unit. */
    public void reportFailure(ReplayUnit unit, Throwable error) {
        Objects.requireNonNull(unit, "unit");
        Objects.requireNonNull(error, "error");
        fail(error, unit);
    }

    private void scheduleWorker() {
        if (!workerScheduled) {
            workerScheduled = true;
            executor.execute(this::runWorker);
        }
    }

    private void runWorker() {
        while (true) {
            synchronized (monitor) {
                if (closed || !advancing(request)) {
                    finishBoundaryRequest();
                    return;
                }
            }

            Optional<ReplayUnit> next;
            synchronized (monitor) {
                next = pending == null ? null : Optional.of(pending);
                pending = null;
            }
            if (next == null) {
                try {
                    next = Objects.requireNonNull(cursor.nextUnit(), "cursor returned null");
                } catch (Throwable error) {
                    fail(error, null);
                    return;
                }
            }
            if (next.isEmpty()) {
                synchronized (monitor) {
                    if (closed || !advancing(request)) {
                        finishBoundaryRequest();
                    } else {
                        request = Request.PAUSE;
                        dateBoundaryOrigin = null;
                        transitionTo(ReplayStatus.COMPLETED);
                        paused = true;
                        workerScheduled = false;
                        monitor.notifyAll();
                    }
                }
                return;
            }

            ReplayUnit unit = next.orElseThrow();
            synchronized (monitor) {
                if (closed || !advancing(request)) {
                    if (!closed) {
                        pending = unit;
                    }
                    finishBoundaryRequest();
                    return;
                }
            }

            try {
                consumer.accept(unit);
            } catch (Throwable error) {
                fail(error, unit);
                return;
            }

            ReplaySpeed delaySpeed;
            synchronized (monitor) {
                if (closed) {
                    workerScheduled = false;
                    monitor.notifyAll();
                    return;
                }
                unitsDelivered++;
                currentReplayDate = unit.replayDate();
                if (advancing(request)) {
                    if (cursor.exhausted()) {
                        request = Request.PAUSE;
                        dateBoundaryOrigin = null;
                        transitionTo(ReplayStatus.COMPLETED);
                        paused = true;
                        workerScheduled = false;
                        monitor.notifyAll();
                        return;
                    }
                }
                if (request == Request.STEP) {
                    request = Request.PAUSE;
                }
                if (request == Request.NEXT_DATE
                        && (dateBoundaryOrigin == null
                        || !dateBoundaryOrigin.equals(unit.replayDate()))) {
                    request = Request.PAUSE;
                }
                if (!advancing(request)) {
                    finishBoundaryRequest();
                    return;
                }
                delaySpeed = request == Request.RUN ? speed : null;
            }

            if (delaySpeed == null) {
                continue;
            }

            try {
                delay.await(delaySpeed);
            } catch (InterruptedException error) {
                Thread.currentThread().interrupt();
                synchronized (monitor) {
                    if (closed) {
                        workerScheduled = false;
                        monitor.notifyAll();
                        return;
                    }
                }
                fail(error, unit);
                return;
            } catch (Throwable error) {
                fail(error, unit);
                return;
            }
        }
    }

    private void finishBoundaryRequest() {
        if (closed || request == Request.CLOSE) {
            transitionTo(ReplayStatus.CLOSED);
            paused = true;
        } else if (request == Request.STOP) {
            transitionTo(ReplayStatus.STOPPED);
            paused = true;
        } else if (request == Request.PAUSE) {
            transitionTo(ReplayStatus.PAUSED);
            paused = true;
        } else if (request == Request.FAIL) {
            transitionTo(ReplayStatus.ERROR);
            paused = true;
        }
        dateBoundaryOrigin = null;
        workerScheduled = false;
        monitor.notifyAll();
    }

    private void fail(Throwable error, ReplayUnit unit) {
        synchronized (monitor) {
            if (closed) {
                return;
            }
            request = Request.FAIL;
            dateBoundaryOrigin = null;
            transitionTo(ReplayStatus.ERROR);
            paused = true;
            failureContext = unit == null ? null : ReplayFailureContext.from(unit);
            errorMessage = error.getMessage() == null
                    ? error.getClass().getName()
                    : error.getClass().getName() + ": " + error.getMessage();
            if (Thread.currentThread() == workerThread) {
                workerScheduled = false;
            }
            monitor.notifyAll();
        }
    }

    private void ensureCanAdvance() {
        ensureOpen();
        if (status == ReplayStatus.COMPLETED || status == ReplayStatus.ERROR) {
            throw new IllegalStateException("replay cannot advance from " + status);
        }
    }

    private void ensureOpen() {
        if (closed) {
            throw new IllegalStateException("replay controller is closed");
        }
    }

    private static boolean advancing(Request request) {
        return request == Request.RUN || request == Request.STEP || request == Request.NEXT_DATE;
    }

    private void transitionTo(ReplayStatus next) {
        if (status == next) {
            return;
        }
        status = next;
        statusHistory.addLast(next);
        if (statusHistory.size() > MAX_STATUS_HISTORY) {
            statusHistory.removeFirst();
        }
    }

    @Override
    public void close() {
        boolean waitForWorker;
        synchronized (monitor) {
            if (closed) {
                return;
            }
            closed = true;
            request = Request.CLOSE;
            transitionTo(ReplayStatus.CLOSED);
            paused = true;
            monitor.notifyAll();
            waitForWorker = Thread.currentThread() != workerThread;
        }
        executor.shutdownNow();
        try {
            cursor.close();
        } finally {
            if (waitForWorker) {
                try {
                    if (!executor.awaitTermination(5, TimeUnit.SECONDS)) {
                        throw new IllegalStateException("replay worker did not terminate");
                    }
                } catch (InterruptedException error) {
                    Thread.currentThread().interrupt();
                    throw new IllegalStateException("interrupted while closing replay controller", error);
                }
            }
        }
    }
}
