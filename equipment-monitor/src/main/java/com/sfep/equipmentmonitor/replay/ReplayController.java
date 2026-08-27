package com.sfep.equipmentmonitor.replay;

import java.util.Objects;
import java.util.Optional;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.TimeUnit;

/** Drives a one-pass replay cursor on one background thread. */
public final class ReplayController implements AutoCloseable {
    private enum Request {
        RUN,
        STEP,
        PAUSE,
        STOP,
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
    private String errorMessage;
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
            status = ReplayStatus.RUNNING;
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
                status = ReplayStatus.STOPPED;
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
            status = ReplayStatus.RUNNING;
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
            return new ReplayControllerState(status, paused, speed, unitsDelivered, errorMessage);
        }
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
                if (closed || (request != Request.RUN && request != Request.STEP)) {
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
                    fail(error);
                    return;
                }
            }
            if (next.isEmpty()) {
                synchronized (monitor) {
                    if (closed || (request != Request.RUN && request != Request.STEP)) {
                        finishBoundaryRequest();
                    } else {
                        request = Request.PAUSE;
                        status = ReplayStatus.COMPLETED;
                        paused = true;
                        workerScheduled = false;
                        monitor.notifyAll();
                    }
                }
                return;
            }

            ReplayUnit unit = next.orElseThrow();
            synchronized (monitor) {
                if (closed || (request != Request.RUN && request != Request.STEP)) {
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
                fail(error);
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
                if (request == Request.RUN || request == Request.STEP) {
                    if (cursor.exhausted()) {
                        request = Request.PAUSE;
                        status = ReplayStatus.COMPLETED;
                        paused = true;
                        workerScheduled = false;
                        monitor.notifyAll();
                        return;
                    }
                }
                if (request == Request.STEP) {
                    request = Request.PAUSE;
                }
                if (request != Request.RUN) {
                    finishBoundaryRequest();
                    return;
                }
                delaySpeed = speed;
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
                fail(error);
                return;
            } catch (Throwable error) {
                fail(error);
                return;
            }
        }
    }

    private void finishBoundaryRequest() {
        if (closed || request == Request.CLOSE) {
            status = ReplayStatus.CLOSED;
            paused = true;
        } else if (request == Request.STOP) {
            status = ReplayStatus.STOPPED;
            paused = true;
        } else if (request == Request.PAUSE) {
            status = ReplayStatus.PAUSED;
            paused = true;
        }
        workerScheduled = false;
        monitor.notifyAll();
    }

    private void fail(Throwable error) {
        synchronized (monitor) {
            if (closed) {
                workerScheduled = false;
                monitor.notifyAll();
                return;
            }
            request = Request.PAUSE;
            status = ReplayStatus.ERROR;
            paused = true;
            errorMessage = error.getMessage() == null
                    ? error.getClass().getName()
                    : error.getClass().getName() + ": " + error.getMessage();
            workerScheduled = false;
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

    @Override
    public void close() {
        boolean waitForWorker;
        synchronized (monitor) {
            if (closed) {
                return;
            }
            closed = true;
            request = Request.CLOSE;
            status = ReplayStatus.CLOSED;
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
