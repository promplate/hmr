"""
Celery worker entry point with HMR integration.

This file demonstrates how to run a Celery worker that can hot-reload
task definitions and other modules without restarting the worker process.
"""

import sys
from atexit import register, unregister
from threading import Thread
from typing import cast

import tasks  # noqa: F401 - Import needed to register tasks with Celery
from app import app
from utils import logger


class CeleryWorkerThread(Thread):
    """Thread to run Celery worker."""

    def __init__(self):
        super().__init__(daemon=True)
        self.worker = None

    def run(self):
        """Start the Celery worker."""
        logger.info("Starting Celery worker...")

        # Create worker instance
        self.worker = app.Worker(
            loglevel='INFO',
            concurrency=1,
            # Important: Don't hijack the root logger to play nice with HMR
            hijack_root_logger=False
        )

        # Start the worker
        try:
            self.worker.start()
        except Exception as e:
            logger.error(f"Worker error: {e}")

    def stop(self):
        """Stop the Celery worker."""
        if self.worker:
            logger.info("Stopping Celery worker...")
            self.worker.stop()


def start_worker():
    """Start or restart the Celery worker."""
    global worker_thread

    logger.info("=" * 50)
    logger.info("Starting Celery HMR Worker")
    logger.info("=" * 50)

    # Stop existing worker if any
    if worker_thread := cast("CeleryWorkerThread | None", globals().get("worker_thread")):
        unregister(worker_thread.stop)
        worker_thread.stop()

    # Start new worker
    worker_thread = CeleryWorkerThread()
    worker_thread.start()
    register(worker_thread.stop)

    logger.info("Worker started! You can now:")
    logger.info("1. Run 'python sender.py' in another terminal to send tasks")
    logger.info("2. Modify tasks.py to see HMR in action")
    logger.info("3. Press Ctrl+C to stop")


if __name__ == "__main__":
    start_worker()

    try:
        # Keep the main thread alive
        while True:
            import time
            time.sleep(1)
    except KeyboardInterrupt:
        logger.info("Shutting down...")
        sys.exit(0)
