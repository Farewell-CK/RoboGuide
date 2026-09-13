package com.elabrador.mobilenavigation;

import org.junit.Test;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicBoolean;
import static org.junit.Assert.*;

public class SingleFrameWorkerTest {
    @Test public void rejectsBacklogAndCloseWaitsForOwnedFrame() throws Exception {
        SingleFrameWorker worker = new SingleFrameWorker();
        CountDownLatch entered = new CountDownLatch(1), release = new CountDownLatch(1);
        AtomicBoolean finished = new AtomicBoolean();
        assertTrue(worker.tryAcquire());
        worker.execute(() -> {
            entered.countDown();
            try { release.await(); } catch (InterruptedException e) { throw new AssertionError(e); }
            finished.set(true);
        });
        assertTrue(entered.await(2, TimeUnit.SECONDS));
        assertFalse(worker.tryAcquire());
        Thread closer = new Thread(worker::close);
        closer.start();
        release.countDown();
        closer.join(2000);
        assertFalse(closer.isAlive());
        assertTrue(finished.get());
    }
}
