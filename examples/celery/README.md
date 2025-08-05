# Celery HMR Example

This example demonstrates how to use HMR (Hot Module Reload) with Celery workers and tasks. With HMR, you can modify task implementations and see the changes take effect immediately without restarting the Celery worker.

## What This Example Shows

- **Hot-reloadable Celery tasks**: Modify task functions and see changes immediately
- **Worker lifecycle management**: Proper integration between HMR and Celery worker threads
- **Real-time task testing**: Send tasks while modifying code to see live updates
- **Error handling**: Demonstrate fixing broken tasks without worker restart

## Files Structure

- `app.py`: Celery app configuration with in-memory broker for demo
- `tasks.py`: Example tasks that can be hot-reloaded
- `worker.py`: Main entry point that starts Celery worker with HMR
- `sender.py`: Script to send test tasks to the worker
- `utils.py`: Logging and utility functions

## How to Run

1. **Install dependencies** (from this directory):
   ```sh
   uv sync
   ```

2. **Start the worker with HMR** (in one terminal):
   ```sh
   hmr worker.py
   ```
   
   You should see output like:
   ```
   Starting Celery HMR Worker
   Worker started! You can now:
   1. Run 'python sender.py' in another terminal to send tasks
   2. Modify tasks.py to see HMR in action
   3. Press Ctrl+C to stop
   ```

3. **Send test tasks** (in another terminal, from this directory):
   ```sh
   python sender.py
   ```

## Demonstrating HMR

Once both the worker and sender are running, try these experiments:

### 1. Modify Task Logic
Edit `tasks.py` and change the `process_data` function. For example:
```python
# Change this line in process_data function:
'doubled': data * 2 if isinstance(data, (int, float)) else f"{data}_{data}",
# To:
'tripled': data * 3 if isinstance(data, (int, float)) else f"{data}_{data}_{data}",
```

Then run `python sender.py` again - you'll see the changes immediately!

### 2. Fix the Failing Task
The `failing_task` function is designed to fail. Fix it by uncommenting the return statement:
```python
@app.task
def failing_task():
    """A task that fails - can be fixed with HMR."""
    logger.info("This task is about to fail...")
    # Uncomment the next line to fix this task:
    return "Task fixed with HMR!"
    # raise Exception("This task always fails - fix me with HMR!")
```

### 3. Add New Tasks
Add a completely new task to `tasks.py`:
```python
@app.task
def new_task(message):
    """A new task added via HMR."""
    logger.info(f"New task received: {message}")
    return f"Processed: {message}"
```

Then modify `sender.py` to call your new task and run it again.

### 4. Update Utilities
Modify `utils.py` to change the logging format or level, and see the changes take effect immediately.

## How It Works

1. **HMR Integration**: The `worker.py` file uses a pattern similar to the Flask example, running the Celery worker in a separate thread that can be restarted when modules change.

2. **Task Registration**: Tasks are automatically discovered and registered with the Celery app. When HMR reloads the tasks module, the new task definitions become available.

3. **In-Memory Broker**: This example uses an in-memory message broker for simplicity. In production, you'd use Redis, RabbitMQ, or another persistent broker.

4. **Thread Safety**: The worker thread is properly managed to ensure clean shutdown and restart when code changes.

## Production Notes

- In production, you'd typically use Redis or RabbitMQ as the message broker
- Consider using `celery beat` for scheduled tasks
- Multiple worker processes can be used for better performance
- The HMR pattern here is primarily for development - in production, you'd use proper deployment strategies

## Troubleshooting

- **Worker not starting**: Make sure no other Celery workers are running on the same broker
- **Tasks not updating**: Ensure you're saving the files and HMR is detecting the changes
- **Import errors**: Check that all modules are properly importing each other

Enjoy experimenting with Celery and HMR! 🚀