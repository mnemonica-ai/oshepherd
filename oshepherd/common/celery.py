import time
import sys


def check_worker_health(celery_app, health_check_interval=30):
    """Periodically check worker Redis connection health"""
    while True:
        try:
            time.sleep(health_check_interval)
            # Test both broker and backend connections
            celery_app.connection().ensure_connection(max_retries=1)
            if hasattr(celery_app.backend, "ensure_connection"):
                celery_app.backend.ensure_connection(max_retries=1)
            print(f" > Worker health check passed")
        except Exception as e:
            print(f" >>> Worker health check failed: {e}")
            print(" >>> Worker connection lost - restarting process...")
            # Exit with code 1 to signal restart needed
            sys.exit(1)
