import os


bind = f"127.0.0.1:{int(os.getenv('AUTH_SERVICE_PORT', '8001'))}"
workers = int(os.getenv("AUTH_GUNICORN_WORKERS", "2"))
threads = int(os.getenv("AUTH_GUNICORN_THREADS", "4"))
timeout = int(os.getenv("AUTH_GUNICORN_TIMEOUT", "120"))
graceful_timeout = 30
accesslog = "-"
errorlog = "-"
capture_output = True
