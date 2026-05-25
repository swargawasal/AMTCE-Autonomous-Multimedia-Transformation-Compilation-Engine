import os
import logging

logger = logging.getLogger("test_tool")

def process_audio_data(data: list):
    """
    BUG: This function assumes data is a list of strings.
    If it receives a None or a non-iterable, it will crash.
    """
    # Intentional bug: if data is None, this will raise TypeError
    return [d.upper() for d in data]
