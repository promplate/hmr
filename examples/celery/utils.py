"""
Utilities for the Celery HMR example.
"""

import logging

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

logger = logging.getLogger('celery_hmr_example')

# This can be modified to change log level or format
logger.info("Utilities module loaded")
