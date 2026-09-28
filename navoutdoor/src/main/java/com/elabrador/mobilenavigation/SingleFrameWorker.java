package com.elabrador.mobilenavigation;

import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicBoolean;

/** No queued backlog. Closing waits for the accepted frame before calibration is destroyed. */
final class SingleFrameWorker implements AutoCloseable {
    private final ExecutorService executor = Executors.newSingleThreadExecutor(
            task -> new Thread(task, "depth-alignment"));
    private final AtomicBoolean busy = new AtomicBoolean();

    boolean tryAcquire() { return busy.compareAndSet(false, true); }
    void execute(Runnable task) {
        try {
            executor.execute(() -> {
                try { task.run(); }
                finally { busy.set(false); }
            });
        } catch (RuntimeException failure) {
            busy.set(false);
            throw failure;
        }
    }
    @Override public void close() {
        executor.shutdown();
        boolean interrupted = Thread.interrupted();
        while (true) {
            try {
                if (executor.awaitTermination(1, TimeUnit.SECONDS)) break;
            } catch (InterruptedException ignored) { interrupted = true; }
        }
        if (interrupted) Thread.currentThread().interrupt();
    }
}
