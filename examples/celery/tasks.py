"""
Celery tasks that can be hot-reloaded with HMR.
"""

from app import app
from utils import logger


@app.task
def add_numbers(x, y):
    """Simple addition task."""
    result = x + y
    logger.info(f"Adding {x} + {y} = {result}")
    return result


@app.task
def process_data(data):
    """Process some data - this can be updated without restarting worker."""
    logger.info(f"Processing data: {data}")

    # This logic can be modified and will be hot-reloaded
    processed = {
        'original': data,
        'length': len(str(data)),
        'doubled': data * 2 if isinstance(data, int | float) else f"{data}_{data}",
        'type': type(data).__name__
    }

    logger.info(f"Processed result: {processed}")
    return processed


@app.task
def slow_task(seconds=5):
    """A task that takes some time - useful for testing HMR during execution."""
    import time

    logger.info(f"Starting slow task that will take {seconds} seconds...")

    for i in range(seconds):
        time.sleep(1)
        logger.info(f"Slow task progress: {i+1}/{seconds}")

    logger.info("Slow task completed!")
    return f"Completed after {seconds} seconds"


@app.task
def failing_task():
    """A task that fails - can be fixed with HMR."""
    logger.info("This task is about to fail...")
    # Uncomment the next line to fix this task:
    # return "Task fixed with HMR!"
    raise Exception("This task always fails - fix me with HMR!")
