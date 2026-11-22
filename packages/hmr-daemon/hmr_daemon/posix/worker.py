from json import dumps
from signal import SIG_IGN, SIGINT, SIGTERM, signal
from threading import Event

from watchfiles import PythonFilter, watch

signal(SIGINT, SIG_IGN)

signal(SIGTERM, lambda *_: shutdown_event.set())

shutdown_event = Event()

for events in watch(".", watch_filter=PythonFilter(), stop_event=shutdown_event):
    try:
        # Serialize entire events set as JSON
        events_data = [(int(event), path) for event, path in events]
        print(dumps(events_data), flush=True)
    except (OSError, BrokenPipeError):
        exit()  # Parent process disconnected
