"""
Task sender script for testing the Celery HMR example.

This script sends various tasks to the worker to demonstrate HMR functionality.
"""

from app import app
from utils import logger


def send_test_tasks():
    """Send a variety of test tasks to demonstrate HMR."""
    logger.info("Sending test tasks to Celery worker...")

    # Task 1: Simple addition
    logger.info("\n1. Sending addition task...")
    result = app.send_task('tasks.add_numbers', args=[10, 5])
    logger.info(f"Task ID: {result.id}")
    logger.info(f"Result: {result.get(timeout=10)}")

    # Task 2: Data processing
    logger.info("\n2. Sending data processing task...")
    result = app.send_task('tasks.process_data', args=["hello world"])
    logger.info(f"Task ID: {result.id}")
    logger.info(f"Result: {result.get(timeout=10)}")

    # Task 3: Numeric data processing
    logger.info("\n3. Sending numeric data processing task...")
    result = app.send_task('tasks.process_data', args=[42])
    logger.info(f"Task ID: {result.id}")
    logger.info(f"Result: {result.get(timeout=10)}")

    # Task 4: Slow task (async)
    logger.info("\n4. Sending slow task (will run in background)...")
    result = app.send_task('tasks.slow_task', args=[3])
    logger.info(f"Task ID: {result.id}")
    logger.info("Slow task started - check worker logs for progress")

    # Task 5: Failing task (to demonstrate error handling)
    logger.info("\n5. Sending failing task...")
    try:
        result = app.send_task('tasks.failing_task')
        logger.info(f"Task ID: {result.id}")
        result.get(timeout=10)
    except Exception as e:
        logger.info(f"Task failed as expected: {e}")

    logger.info("\n" + "="*50)
    logger.info("All test tasks sent!")
    logger.info("Try modifying tasks.py and running this script again")
    logger.info("to see HMR in action.")
    logger.info("="*50)


if __name__ == "__main__":
    send_test_tasks()
