"""
Celery app configuration for HMR example.
"""

from celery import Celery

# Create Celery instance
app = Celery('hmr_celery_example')

# Configure Celery
app.conf.update(
    broker_url='memory://',  # Use in-memory broker for demo
    result_backend='cache+memory://',  # Use in-memory result backend
    task_serializer='json',
    accept_content=['json'],
    result_serializer='json',
    timezone='UTC',
    enable_utc=True,
    # Important: Set this to False to allow task updates without restart
    worker_hijack_root_logger=False,
)

# Auto-discover tasks
app.autodiscover_tasks(['tasks'])
