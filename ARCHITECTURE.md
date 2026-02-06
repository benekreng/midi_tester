# MIDI Device Emulator - Architecture & Capabilities Documentation

## Overview

This is a **Python-based MIDI emulator and stress testing tool** designed to send and receive complex MIDI messages, including exotic and advanced MIDI parameter types. It provides a graphical interface for manipulating MIDI parameters and testing MIDI device behavior with feedback loops and latency simulation.

### Purpose
- Emulate MIDI devices for testing and development
- Send complex MIDI messages (NRPN, 14-bit CC, Pitch Bend, etc.)
- Test MIDI implementations with feedback loops and stress testing
- Visualize incoming MIDI data in real-time

---

## Architecture Overview

The application follows a **modular, three-layer architecture**:

```
┌─────────────┐
│   main.py   │  ← Entry point & main event loop
└──────┬──────┘
       │
       ├──────────────────────────────────────┐
       │                                      │
┌──────▼──────────┐                  ┌───────▼────────┐
│  gui.py         │◄─────────────────┤ processor.py   │
│  (AppGui)       │                  │ (MidiProcessor)│
└──────┬──────────┘                  └───────┬────────┘
       │                                      │
       │          ┌───────────────────────────┘
       │          │
┌──────▼──────────▼────┐
│  midi_backend.py     │
│  (MidiBackend)       │
└──────────────────────┘
```

### Module Breakdown

#### 1. **main.py** - Application Entry Point & Event Loop
**Responsibilities:**
- Initialize all components (MidiBackend, MidiProcessor, AppGui)
- Run the main event loop
- Coordinate data flow between components
- Handle GUI rendering

**Event Loop Flow:**
```python
while running:
    1. Poll MIDI hardware for incoming messages
    2. Process messages through MidiProcessor (feedback/delay logic)
    3. Flush scheduled events (delayed feedback)
    4. Update GUI with incoming data
    5. Render frame
```

**Key Features:**
- Channel filtering (only update GUI for selected channel or "Omni")
- Automatic knob updates from incoming MIDI
- Note/velocity display
- Event logging

---

#### 2. **midi_backend.py** - MIDI I/O & Protocol Layer
**Responsibilities:**
- Hardware/virtual MIDI port management
- Low-level MIDI message sending
- MIDI message parsing and assembly
- Protocol-level state management (NRPN, 14-bit CC parsing)
- High-precision capture via callback queue (timestamps on arrival)

**Key Classes:**
- `MidiBackend` - Main MIDI communication class

**MIDI Port Modes:**
1. **Physical Mode**: Connect to hardware MIDI ports
2. **Virtual Mode**: Create virtual MIDI ports (macOS/Linux native, Windows needs LoopMIDI)
   - Port name: "Python Emulator"

**Sending Capabilities:**
- ✅ Standard CC (Control Change) - 7-bit
- ✅ 14-bit CC (High-resolution CC using MSB/LSB pairs)
- ✅ NRPN (Non-Registered Parameter Number) - 14-bit parameters
- ✅ Pitch Bend - 14-bit (-8192 to +8191)
- ✅ Program Change
- ✅ Channel Aftertouch (monophonic aftertouch)
- ✅ Note On/Off messages
- ❌ NOT IMPLEMENTED: Polyphonic Aftertouch sending
- ❌ NOT IMPLEMENTED: SysEx messages
- ❌ NOT IMPLEMENTED: RPN (Registered Parameter Numbers)
- ❌ NOT IMPLEMENTED: Note Off with release velocity

**Receiving Capabilities:**
- ✅ Notes (Note On/Off)
- ✅ Polyphonic Aftertouch (polytouch) - PARSED but not echoed
- ✅ Channel Aftertouch
- ✅ Pitch Bend
- ✅ Program Change
- ✅ Control Change (7-bit)
- ✅ 14-bit CC (stateful parsing of MSB/LSB pairs)
- ✅ NRPN (stateful multi-message parsing)
- ❌ NOT IMPLEMENTED: RPN parsing
- ❌ NOT IMPLEMENTED: SysEx parsing
- ❌ NOT IMPLEMENTED: MIDI Clock/Timing messages
- ❌ NOT IMPLEMENTED: MPE (MIDI Polyphonic Expression)

**Protocol Parsing Details:**

**NRPN Parsing:**
```
State Machine:
1. CC 99 (NRPN MSB) → store in _nrpn_msb
2. CC 98 (NRPN LSB) → store in _nrpn_lsb
3. CC 6 (Data Entry MSB) → emit NRPN event with 14-bit value
4. CC 38 (Data Entry LSB) → emit complete NRPN event
5. CC 100/101 (RPN) → reset NRPN state
```

**14-bit CC Parsing:**
```
State Machine:
1. CC 0-31 (MSB) → store CC number and value
2. CC 32-63 (LSB) → if matches previous MSB-32, emit 14-bit CC event
   Otherwise, treat as separate 7-bit CC
```

**MIDI Value Conversions:**
- Pitch Bend: `raw_value = mido_pitch + 8192` (0-16383 range)
- 14-bit values: `(MSB << 7) | LSB`
- NRPN address: `(MSB << 7) | LSB`

**Input Capture Path:**
- Input ports use `mido.open_input(..., callback=...)` to timestamp messages immediately.
- Callback pushes raw messages into a bounded queue.
- `poll_messages()` drains the queue and runs the protocol parser on the main thread.

---

#### 3. **processor.py** - MIDI Processing & Feedback Logic
**Responsibilities:**
- Implement feedback modes (immediate/delayed echo)
- Schedule delayed MIDI events
- Filter events for processing

**Key Classes:**
- `MidiProcessor` - Handles event processing and scheduling

**Feedback Modes:**
1. **NONE**: Pass-through only, no echo
2. **IMMEDIATE**: Echo incoming MIDI back out instantly (stress testing)
3. **DELAYED**: Echo with configurable delay (1-2000ms) using time-based scheduling

**Scheduling Algorithm:**
```python
scheduled_events = deque()  # (release_time, event_data)
- Events appended in chronological order
- Poll each frame: check if oldest event is ready
- If ready, send and pop from queue
- If not ready, break (all remaining events are future)
```

**Event Filtering:**
- Notes and Polyphonic Aftertouch are NOT echoed (intentional design choice)
- All parameter changes (CC, NRPN, PB, AT, PC) are echoed based on mode

---

#### 4. **message_types.py** - Message Abstractions & Identity
**Responsibilities:**
- Define a uniform `MessageSpec` (type, channel, number, value)
- Provide identity mapping for latency matching
- Provide default probe values and random generation
- Centralize message type labels and ranges

**Key Types:**
- `MessageSpec`
- `TYPE_*` constants (note, cc, cc14, nrpn, pc, pb)

---

#### 5. **timing_model.py** - Timing Model
**Responsibilities:**
- Generate variable delays between fuzz messages
- Support preset timing modes (steady/jitter/burst/chaos)
- Support full timing controls (rate, jitter, burst, min/max gap)

---

#### 6. **endurance_monitor.py** - Endurance Response Monitor
**Responsibilities:**
- Periodically send a probe chord at a fixed interval
- Measure round-trip latency and inter-event dispersion for each probe
- Track long-running results with bounded history for plotting
- Detect “strumming” (dispersion > 0) over time
- Probe multiple message types (notes, CC, CC14, NRPN, PC, PB)

**Key Classes:**
- `EnduranceLatencyMonitor` - Probe scheduler and analyzer

**Metrics:**
- **Round Trip Latency**: First message received minus send time
- **Inter-Event Dispersion**: Last message received minus first message received

---

#### 7. **fuzz_test.py** - Fuzz Generator & Analyzer
**Responsibilities:**
- Generate unique, non-overlapping message identities
- Schedule variable timing (bursts + jitter)
- Match returned messages and compute RTT statistics
- Track missing messages and log them

**Key Components:**
- `FuzzGenerator` - message creation + scheduling
- `FuzzAnalyzer` - matching + stats + missing log

---

#### 8. **gui.py** - User Interface & Visualization
**Responsibilities:**
- Render DearPyGUI interface
- Handle user input
- Display real-time MIDI data
- Log MIDI events

**Key Classes:**
- `AppGui` - Main GUI management class

**GUI Layout:**

**Section 1: Connections**
- Virtual Mode toggle
- Port refresh button
- Channel selector (1-16 or "Omni")
- Input/Output port dropdowns (hidden in virtual mode)

**Section 2: Stress Test & Feedback**
- Feedback mode selector (None/Immediate/Delayed)
- Delay slider (1-2000ms)
- **Test Notes button**: Sends notes 0x55 (85) and 0x2A (42) with velocity 100
  - Used for automated testing (marks midiOut1Passed and midiOut2Passed)

**Section 3: Parameters**
Vertical sliders for 7 MIDI parameters:
```python
PARAMS = {
    "cutoff":    {"type": "cc",   "num": 74,   "max": 127},     # Filter Cutoff
    "res":       {"type": "cc",   "num": 71,   "max": 127},     # Resonance
    "mod_hr":    {"type": "cc14", "num": 1,    "max": 16383},   # Mod Wheel (14-bit)
    "decay":     {"type": "nrpn", "num": 1300, "max": 16383},   # NRPN Decay
    "pb":        {"type": "pb",   "num": 0,    "max": 16383},   # Pitch Bend
    "at":        {"type": "at",   "num": 0,    "max": 127},     # Aftertouch
    "prog":      {"type": "pc",   "num": 0,    "max": 127},     # Program Change
}
```

**Section 4: Monitors & Log**
- Note/Velocity display
- Polyphonic Aftertouch display (parsed but not used elsewhere)
- Scrolling log window (max 20 entries)

**Endurance Test Tab:**
- Start/stop endurance probe loop
- Configure probe interval, note list, and message types
- **Plot 1:** Inter-Event Dispersion (ms) vs. Test Duration (min)
- **Plot 2:** Per-message offset (ms) vs. Test Duration (min)

**Fuzz Stress Test Tab:**
- Generate unique, non-overlapping message identities
- Preset timing modes (steady/jitter/burst/chaos) or full timing controls
- Mixed, single-type, or chaos message generation modes
- Live mean/std/min/max RTT metrics
- Missing-message log

**GUI Features:**
- ✅ Real-time knob updates from incoming MIDI (bidirectional)
- ✅ Channel filtering (only show selected channel)
- ✅ Port auto-refresh
- ✅ Virtual port status indicator
- ✅ Global font scaling (1.25x)
- ❌ NOT IMPLEMENTED: Knob MIDI learn
- ❌ NOT IMPLEMENTED: MIDI recording/playback
- ❌ NOT IMPLEMENTED: Preset management
- ❌ NOT IMPLEMENTED: Multiple parameter pages

---

#### 9. **settings_store.py** - Persistent Settings
**Responsibilities:**
- Persist user settings to `settings.json`
- Restore last-used input/output ports and key test configurations
- Provide per-tab reset-to-default behavior

**Callback Prevention:**
- `update_knob_from_midi()` updates GUI values WITHOUT triggering send callbacks
- Prevents infinite feedback loops when receiving MIDI

---

## Data Flow

### Outgoing MIDI (User → Device)
```
User moves slider
    ↓
gui.knob_cb() 
    ↓
Get selected channel
    ↓
Call appropriate midi.send_*() method
    ↓
MidiBackend formats message
    ↓
mido.Message sent to output port
```

### Incoming MIDI (Device → GUI)
```
MIDI hardware
    ↓
midi.poll_messages() - parse raw mido messages
    ↓
processor.process_incoming_events() - apply feedback logic
    ↓
main loop filters by channel
    ↓
gui.update_knob_from_midi() - update sliders
    ↓
Display in log
```

### Feedback Loop (Stress Testing)
```
Incoming MIDI
    ↓
processor.process_incoming_events()
    ↓
if IMMEDIATE: midi.send_event_struct() → instant echo
if DELAYED: scheduled_events.append(time, event)
    ↓
processor.process_scheduled_events() - poll each frame
    ↓
if time >= release_time: midi.send_event_struct()
```

---

## Dependencies

```
mido           - MIDI I/O library (pure Python MIDI implementation)
python-rtmidi  - Real-time MIDI backend (C++ bindings for low-latency)
dearpygui      - Immediate-mode GUI framework
```

**Platform Support:**
- **macOS/Linux**: Full virtual port support
- **Windows**: Virtual ports require LoopMIDI or similar virtual MIDI driver

---

## Current Capabilities

### ✅ What It CAN Do

**MIDI Protocol:**
- Send/receive standard MIDI messages (CC, PC, PB, AT, Notes)
- Send/receive 14-bit high-resolution Control Changes
- Send/receive NRPN (Non-Registered Parameter Numbers)
- Parse complex multi-message MIDI protocols (stateful parsing)
- Handle MIDI on any channel (1-16)
- Create virtual MIDI ports

**Testing & Stress:**
- Echo MIDI back immediately (stress test devices with instant feedback)
- Echo MIDI with configurable delay (test latency handling)
- Send test notes for automated validation
- Real-time monitoring and logging

**User Interface:**
- Visual parameter control with sliders
- Bidirectional MIDI (GUI updates from incoming MIDI)
- Channel filtering
- Real-time MIDI log

### ❌ What It CANNOT Do

**MIDI Protocol Limitations:**
- No RPN (Registered Parameter Numbers) support
- No SysEx (System Exclusive) messages
- No MIDI Clock/Sync/Timing messages
- No MPE (MIDI Polyphonic Expression)
- No per-note polyphonic aftertouch sending
- No MTC (MIDI Time Code)
- No MMC (MIDI Machine Control)

**Testing Limitations:**
- No MIDI recording/playback
- No automated test sequences
- No MIDI file import/export
- No preset management
- No parameter randomization
- No bulk CC sweep/fuzz testing
- No MIDI monitor/sniffer mode (can't monitor without affecting)

**GUI Limitations:**
- Fixed parameter set (7 parameters hardcoded)
- No MIDI learn functionality
- No parameter grouping/pages
- No visual MIDI activity indicators (meters/LEDs)
- No save/load of GUI state
- No keyboard shortcuts
- No drag-and-drop MIDI routing

**Architecture Limitations:**
- Single-threaded (GUI and MIDI share event loop)
- No plugin system
- No scripting/automation API
- No multiple MIDI input/output pairs
- No MIDI routing/filtering rules

---

## Extension Opportunities

### Easy Extensions (Low Complexity)

1. **Add More Parameters**
   - Add entries to `PARAMS` dict in `gui.py`
   - GUI automatically generates sliders

2. **Add More Test Buttons**
   - Follow pattern of `send_test_notes_cb()`
   - Add button in GUI build section

3. **Custom MIDI Sequences**
   - Add methods to MidiBackend for specific sequences
   - Create GUI buttons to trigger them

4. **Expanded Logging**
   - Add file logging to `log_midi()`
   - Add timestamp display
   - Add log filtering

### Medium Extensions (Moderate Complexity)

1. **RPN Support**
   - Add RPN parsing to `poll_messages()` (similar to NRPN)
   - Add `send_rpn()` method
   - Add RPN parameters to GUI

2. **Preset Management**
   - Add JSON serialization of parameter values
   - Add save/load buttons
   - Store presets in files

3. **MIDI Learn**
   - Add "learn" mode flag
   - Capture next incoming CC/NRPN
   - Bind to selected parameter

4. **Multiple Pages**
   - Add page selector
   - Load different PARAMS sets per page
   - Rebuild slider section on page change

5. **Parameter Randomization**
   - Add "randomize" button
   - Generate random values within ranges
   - Send all parameters

6. **Activity Indicators**
   - Add LED-style indicators for MIDI I/O
   - Flash on send/receive
   - Add to GUI layout

### Hard Extensions (High Complexity)

1. **SysEx Support**
   - Requires manufacturer-specific parsing
   - Add hex editor for SysEx construction
   - Add SysEx library/templates

2. **MIDI Recording/Playback**
   - Add timeline data structure
   - Record timestamp + message
   - Playback with precise timing
   - Save to MIDI file format

3. **Multiple I/O Pairs**
   - Refactor MidiBackend to support multiple ports
   - Add routing matrix in GUI
   - Handle separate feedback per port

4. **Plugin Architecture**
   - Define plugin API (message processing, GUI panels)
   - Add plugin loader
   - Allow dynamic extension without code changes

5. **MPE Support**
   - Track per-note pitch bend, CC, AT
   - Add MPE zone configuration
   - Add MPE-specific GUI (multi-note visualization)

6. **Scripting API**
   - Add Python script loading
   - Expose MIDI and GUI objects to scripts
   - Allow automation and custom behaviors

7. **MIDI Monitor Mode**
   - Add passive monitoring without port ownership
   - Requires different mido port strategy
   - Add filtering and message decode

8. **Advanced Stress Testing**
   - CC sweep (cycle through all CCs)
   - Note flood (send burst of notes)
   - Jitter testing (random timing variations)
   - Message queue depth testing

---

## Code Quality & Patterns

**Good Patterns Used:**
- ✅ Clear separation of concerns (GUI/Logic/I/O)
- ✅ Declarative parameter configuration (`PARAMS` dict)
- ✅ State machine parsing for complex MIDI protocols
- ✅ Callback-based GUI (DearPyGUI pattern)
- ✅ Event queue for delayed actions (deque)

**Areas for Improvement:**
- ⚠️ No error handling on MIDI send failures
- ⚠️ No unit tests
- ⚠️ No configuration file (all settings hardcoded)
- ⚠️ No logging framework (print statements + GUI log only)
- ⚠️ Global font scale in main (could be configurable)
- ⚠️ Magic numbers (20 log entries, 8192 pitch bend center)

---

## Technical Constraints

**Performance:**
- Main loop runs at GUI frame rate (~60 FPS)
- MIDI polling is non-blocking (`iter_pending()`)
- Delayed events checked every frame (O(1) amortized)

**Memory:**
- Scheduled events stored in unbounded deque (could grow infinitely)
- Log entries capped at 20 (automatic cleanup)
- No MIDI message buffering (process immediately)

**Timing Accuracy:**
- Delay timing uses `time.perf_counter()` (high precision)
- Limited by frame rate (max ~16ms jitter at 60 FPS)
- Not suitable for sample-accurate timing

---

## Entry Points for AI Extending This Code

When planning extensions, consider:

1. **Start with `PARAMS` dict** - Easiest extension point for new parameters
2. **MidiBackend is the protocol layer** - Add new MIDI message types here first
3. **Processor is the logic layer** - Add new processing modes/algorithms here
4. **GUI is the presentation layer** - Add new controls/visualizations here
5. **Main loop coordinates** - Modify data flow and update logic here

**Key Files to Modify:**
- Adding parameters: `gui.py` (PARAMS dict)
- Adding MIDI messages: `midi_backend.py` (send_* and poll_messages)
- Adding processing: `processor.py` (new modes or filters)
- Changing data flow: `main.py` (event loop coordination)

**Gotchas to Watch:**
- DearPyGUI uses tags (strings) to reference widgets
- MIDI channels are 0-indexed in mido, 1-indexed in GUI
- Pitch bend center is 8192 (not 0)
- 14-bit values are MSB<<7 | LSB
- Callbacks can create infinite loops if not careful
- Virtual ports may not work on Windows without extra software

---

## Summary

This is a **functional, well-structured MIDI emulator** focused on **exotic MIDI parameters** and **stress testing**. It handles complex MIDI protocols (NRPN, 14-bit CC) that many tools ignore. The architecture is clean and modular, making it easy to extend with new parameters, test modes, or MIDI message types.

**Best suited for:**
- Testing MIDI device implementations
- Sending complex MIDI messages not available in standard tools
- Stress testing with feedback loops and latency
- Learning MIDI protocol internals

**Not suited for:**
- Production MIDI performance (no robust error handling)
- MIDI sequencing/composition (no timeline)
- SysEx programming (not implemented)
- Large-scale MIDI routing (single I/O pair only)

The codebase is approximately **600 lines** across 4 Python files, making it digestible for AI analysis and extension planning.
