# OXI E16 Remote Mode SysEx Protocol

## Overview

Remote mode allows a host application to control the E16's OLED display and LED rings over USB SysEx, while receiving encoder/button input as standard MIDI messages.

## SysEx Header

All messages share the same header:

| Byte | Value | Description |
|------|-------|-------------|
| 0 | `F0` | SysEx Start |
| 1-3 | `00 21 5B` | OXI Instruments Manufacturer ID |
| 4-5 | `02 01` | E16 Product ID |

Followed by the category byte (`0x06`), the message ID, then payload, then `F7` (SysEx End). The category byte `0x06` is required in every remote mode message.

## Data Encoding

Payload data after the command bytes uses **7-bit packing**: every group of 7 data bytes is preceded by a "top bits" byte that carries the MSB of each subsequent byte. This ensures all bytes on the wire are < `0x80` as required by SysEx.

## Messages

### Enter Remote Mode

Sent by the host to enter remote mode on the device.

| Field | Value |
|-------|-------|
| MSG | `ENTER REMOTE MODE` |
| ID | `0x06 0x55` |
| Structure | No payload |
| Example | `F0 00 21 5B 02 01 06 55 F7` |

---

### Remote Mode Entered ACK

Sent by the device to acknowledge entering remote mode.

| Field | Value |
|-------|-------|
| MSG | `REMOTE MODE ENTERED ACK` |
| ID | `0x06 0x53` |
| Structure | No payload |
| Example | `F0 00 21 5B 02 01 06 53 F7` |

---

### Exit Remote Mode

Sent by the host to exit remote mode. The category byte (`0x06`) is still required.

| Field | Value |
|-------|-------|
| MSG | `EXIT REMOTE MODE` |
| ID | `0x06 0x00` |
| Structure | No payload |
| Example | `F0 00 21 5B 02 01 06 00 F7` |

---

### LED

Sets individual LEDs with specific RGB values. Supports multiple LEDs in a single message using variable-length 5-byte chunks.

| Field | Value |
|-------|-------|
| MSG | `LED` |
| ID | `0x06 0x01` |
| Structure | Variable length, 5-byte chunks (7-bit packed) |

**Each 5-byte chunk (raw, before packing):**

| Byte | Description |
|------|-------------|
| Encoder index | 1 byte (0-15, which encoder ring) |
| LED index | 1 byte (0-15, which LED in the ring) |
| R | 1 byte (0-127) |
| G | 1 byte (0-127) |
| B | 1 byte (0-127) |

Multiple LEDs can be set in a single message by appending additional 5-byte chunks to the raw payload before 7-bit packing.

**Example (2 LEDs):**

Raw payload (10 bytes):
```
[00 00 7F 00 00] [04 07 00 7F 00]
 Enc0 LED0 R G B  Enc4 LED7 R G B
```

7-bit packed (the packing groups span across chunk boundaries):
```
F0 00 21 5B 02 01 06 01 [packed 10 bytes → 12 wire bytes] F7
```

---

### LED Ring

Sets an encoder's LED ring using arc or bipolar rendering. Supports multiple encoders in a single message using variable-length 7-byte chunks.

| Field | Value |
|-------|-------|
| MSG | `LED RING` |
| ID | `0x06 0x04` |
| Structure | Variable length, 7-byte chunks (7-bit packed) |

**Each 7-byte chunk (raw, before packing):**

| Byte | Description |
|------|-------------|
| Encoder index | 1 byte (0-15) |
| R | 1 byte (0-127) |
| G | 1 byte (0-127) |
| B | 1 byte (0-127) |
| Ring amount MSB | 1 byte (0-127) |
| Ring amount LSB | 1 byte (0-127) |
| Bipolar | 1 byte (0x00 = normal arc, 0x01 = bipolar centered) |

Ring amount is a 14-bit value (MSB << 7 | LSB), range 0-16383, mapping to 0-100% of the ring.

In normal mode (`bipolar = 0`), the ring fills as an arc from 0 to the specified amount.

In bipolar mode (`bipolar = 1`), the ring renders centered: values below 50% extend left, values above 50% extend right.

Multiple encoders can be set in a single message by appending additional 7-byte chunks to the raw payload before 7-bit packing.

**Example (1 encoder, red, 50%, normal):**

Raw payload (7 bytes):
```
[00 7F 00 00 40 00 00]
 Enc R  G  B  MSB LSB Bipolar
```

```
F0 00 21 5B 02 01 06 04 [packed 7 bytes → 8 wire bytes] F7
```

---

### OLED Framebuffer

Sends a full raw framebuffer to the OLED display (128×64 pixels, 1 bit per pixel).

| Field | Value |
|-------|-------|
| MSG | `OLED FRAMEBUFFER` |
| ID | `0x06 0x02` |
| Structure | 1024 bytes of raw display data (7-bit packed) |

**Payload:** 1024 bytes representing the 128×64 monochrome display buffer. Pixel layout matches the SSD1306 page/column format. This replaces the entire display content — partial updates are not supported.

**Example:**

```
F0 00 21 5B 02 01 06 02 [packed 1024 bytes → 1171 wire bytes] F7
```

---

### OLED Labels

Sends text labels for the 16 encoders plus a title string. The device renders these using its built-in knob menu layout.

| Field | Value |
|-------|-------|
| MSG | `OLED LABELS` |
| ID | `0x06 0x03` |
| Structure | 16-char title + 16 × 4-char labels (7-bit packed) |

**Payload (80 bytes raw, before packing):**

| Offset | Length | Description |
|--------|--------|-------------|
| 0 | 16 | Title string (displayed at top of screen) |
| 16 | 4 | Label for encoder 0 |
| 20 | 4 | Label for encoder 1 |
| ... | ... | ... |
| 76 | 4 | Label for encoder 15 |

Strings are fixed-length, padded with spaces or null bytes.

**Example:**

```
F0 00 21 5B 02 01 06 03 [packed 80 bytes → 92 wire bytes] F7
```

Sending a framebuffer overrides labels and vice versa — whichever arrives last is displayed.

---

## Device → Host: Encoder & Button Events

While in remote mode, the device sends **standard MIDI messages** (not SysEx) on USB Output 1:

### Encoder Turns

Sent as **Control Change** on MIDI channel 1:

| Parameter | Value |
|-----------|-------|
| Channel | 1 (0x00) |
| CC Number | Encoder index + 1 (1-16) |
| Value | Clockwise: 1-15, Counter-clockwise: `0x10 + negative_inc` (e.g., -1 → 0x0F, -2 → 0x0E) |

This uses a relative encoding scheme:
- **Clockwise:** Value = increment amount (1-15)
- **Counter-clockwise:** Value = `0x10 + increment` where increment is negative (e.g., inc=-1 → 0x10+(-1) = 0x0F)

### Button Presses

| Event | MIDI Message | Channel | Note | Velocity |
|-------|-------------|---------|------|----------|
| Press | Note On | 1 | 0-15 (button index) | 127 |
| Release | Note Off | 1 | 0-15 (button index) | 0 |
| SHIFT Press | Note On | 1 | 16 | 127 |
| SHIFT Release | Note Off | 1 | 16 | 0 |

The SHIFT button always sends note 16 in remote mode. To exit remote mode, the host sends the Exit Remote Mode SysEx message.

---

## Message ID Summary

| Message | Category | ID | Direction |
|---------|----------|----|-----------|
| Enter Remote Mode | `0x06` | `0x55` | Host → Device |
| ACK (entered) | `0x06` | `0x53` | Device → Host |
| Exit Remote Mode | `0x06` | `0x00` | Host → Device |
| LED | `0x06` | `0x01` | Host → Device |
| LED Ring | `0x06` | `0x04` | Host → Device |
| OLED Framebuffer | `0x06` | `0x02` | Host → Device |
| OLED Labels | `0x06` | `0x03` | Host → Device |

> **Note:** The category byte `0x06` is required in every remote mode message. Messages are dispatched by their sub-message ID within the remote category.
