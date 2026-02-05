import time
from collections import deque

class MidiProcessor:
    def __init__(self, midi_backend):
        self.midi = midi_backend
        
        # Configuration
        self.feedback_mode = "NONE" # Options: "NONE", "IMMEDIATE", "DELAYED"
        self.delay_ms = 0
        
        # Scheduling Buffer: List of tuples (release_time, event_data)
        self.scheduled_events = deque()

    def set_feedback_mode(self, mode):
        """Sets mode: NONE, IMMEDIATE, or DELAYED"""
        self.feedback_mode = mode
        # Clear buffer if switching modes so we don't have hanging notes
        self.scheduled_events.clear()

    def set_delay(self, ms):
        self.delay_ms = max(0, ms)

    def process_incoming_events(self, events):
        """
        Takes events from MidiBackend, processes them for feedback,
        and returns them for the GUI to consume.
        """
        current_time = time.perf_counter()
        
        for event in events:
            # LOGIC: Echo back?
            if self.feedback_mode == "IMMEDIATE":
                # Fire back out of all holes immediately
                self.midi.send_event_struct(event)
                
            elif self.feedback_mode == "DELAYED":
                # Schedule for later
                release_time = current_time + (self.delay_ms / 1000.0)
                self.scheduled_events.append((release_time, event))
                
        return events

    def process_scheduled_events(self):
        """
        Call this every frame. Checks if delayed events are ready to fire.
        """
        if self.feedback_mode != "DELAYED" or not self.scheduled_events:
            return

        current_time = time.perf_counter()
        
        # events are appended in chronological order, so we can peek at the left
        while self.scheduled_events:
            release_time, event = self.scheduled_events[0]
            
            if current_time >= release_time:
                self.midi.send_event_struct(event)
                self.scheduled_events.popleft()
            else:
                # If the oldest event isn't ready, none of them are
                break