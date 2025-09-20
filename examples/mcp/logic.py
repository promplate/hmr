from datetime import datetime


def greet(name: str) -> str:
    # Edit this function while the server is running to see HMR in action
    return f"Hello, {name}! The time is {datetime.now():%H:%M:%S}"

