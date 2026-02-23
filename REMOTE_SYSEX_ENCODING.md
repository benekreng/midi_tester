# OXI E16 Remote Mode: SysEx Data Encoding Guide

This document explains **exactly** how raw data bytes are encoded before being sent inside SysEx messages, and how to decode them on the receiving end. This is the missing piece you need to correctly build and parse remote mode messages.

---

## Why Encoding Is Needed

MIDI SysEx has one hard rule: **every byte between `F0` (start) and `F7` (end) must be less than `0x80`** (i.e., bit 7 must be 0). Only values `0x00`–`0x7F` are legal.

But real data (like RGB values, pixel data, etc.) can be any value `0x00`–`0xFF`. So we need a way to squeeze 8-bit data into 7-bit-safe bytes. The E16 uses **two** encoding schemes depending on the message type. For **remote mode**, it uses the **7-bit packing** scheme (NOT nibblization).

---

## Scheme 1: 7-Bit Packing (Used by Remote Mode)

### The Concept

Take your data bytes in groups of 7. For each group:

1. **Extract bit 7** (the MSB, the "forbidden" bit) from each of the 7 bytes
2. **Pack those 7 MSBs into a single "top-bits" byte** (bit 0 = MSB of byte 1, bit 1 = MSB of byte 2, etc.)
3. **Send the top-bits byte first**, followed by the 7 data bytes with their MSBs stripped (masked to `& 0x7F`)

This means **7 data bytes become 8 wire bytes** (1 top-bits byte + 7 stripped data bytes). Overhead is ~14%.

### Encoding Step-by-Step

**Input:** An array of raw data bytes you want to send.

**Process:**

```
For each group of up to 7 bytes from your data:

    top_bits_byte = 0x00

    For i = 0 to 6 (or until data runs out):
        if data_byte[i] has bit 7 set (value >= 0x80):
            set bit i in top_bits_byte
        strip bit 7 from data_byte[i]  (data_byte[i] &= 0x7F)

    Output: [top_bits_byte] [stripped_byte_0] [stripped_byte_1] ... [stripped_byte_6]
```

### Worked Example: Encoding

Say you want to send these 5 raw bytes (a generic data payload to demonstrate MSB handling):

```
Raw data:  0x03  0x07  0xFF  0x00  0x80
```

**Step 1:** Group them (only 5 bytes, less than 7, so one group).

**Step 2:** Extract MSBs:
- Byte 0: `0x03` → bit 7 = 0
- Byte 1: `0x07` → bit 7 = 0
- Byte 2: `0xFF` → bit 7 = **1**
- Byte 3: `0x00` → bit 7 = 0
- Byte 4: `0x80` → bit 7 = **1**

**Step 3:** Build top-bits byte:
```
top_bits = (0 << 0) | (0 << 1) | (1 << 2) | (0 << 3) | (1 << 4)
         = 0b00010100
         = 0x14
```

**Step 4:** Strip MSBs from data bytes:
```
0x03 & 0x7F = 0x03
0x07 & 0x7F = 0x07
0xFF & 0x7F = 0x7F
0x00 & 0x7F = 0x00
0x80 & 0x7F = 0x00
```

**Result on the wire:**
```
0x14  0x03  0x07  0x7F  0x00  0x00
^     ^---- 5 stripped data bytes ---^
|
top-bits byte
```

All bytes are < `0x80`. SysEx-safe.

### Decoding Step-by-Step

**Input:** The encoded wire bytes.

**Process:**

```
Read one byte → this is the top_bits_byte
Read up to 7 bytes → these are the stripped data bytes

For i = 0 to (number of stripped bytes - 1):
    msb = (top_bits_byte >> i) & 0x01       // extract bit i
    original_byte = stripped_byte[i] | (msb << 7)  // restore MSB
```

### Worked Example: Decoding

Wire bytes received: `0x14 0x03 0x07 0x7F 0x00 0x00`

**Step 1:** First byte is `top_bits = 0x14 = 0b00010100`

**Step 2:** Remaining bytes: `0x03 0x07 0x7F 0x00 0x00`

**Step 3:** Restore each byte:
```
i=0: msb = (0x14 >> 0) & 1 = 0 → 0x03 | (0 << 7) = 0x03
i=1: msb = (0x14 >> 1) & 1 = 0 → 0x07 | (0 << 7) = 0x07
i=2: msb = (0x14 >> 2) & 1 = 1 → 0x7F | (1 << 7) = 0xFF  ✓
i=3: msb = (0x14 >> 3) & 1 = 0 → 0x00 | (0 << 7) = 0x00
i=4: msb = (0x14 >> 4) & 1 = 1 → 0x00 | (1 << 7) = 0x80  ✓
```

**Decoded data:** `0x03 0x07 0xFF 0x00 0x80` — matches the original.

> **Note:** This example uses values > 0x7F to demonstrate MSB packing. For remote mode LED messages specifically, RGB values are 0-127, so the top-bits byte will always be `0x00`. The encoding is still required.

### Handling Data Longer Than 7 Bytes

If your payload is longer than 7 bytes, you repeat the process for each group of 7:

```
Data: [byte0..byte6] [byte7..byte13] [byte14..byte16]

Wire: [top0] [b0..b6]  [top1] [b7..b13]  [top2] [b14..b16]
       8 bytes           8 bytes            4 bytes (3 data + 1 top)
```

The last group can be shorter than 7 bytes. The top-bits byte still comes first, but only the bits corresponding to actual data bytes are meaningful (unused bits are 0).

### Edge Case: All Values Are 0x00–0x7F

If none of your data bytes have bit 7 set, the top-bits byte is simply `0x00`. You still MUST include it. The format is always `[top_bits] [data...]`, never just `[data...]`.

---

## Scheme 2: 4-Bit Nibblization (NOT Used by Remote Mode)

This is included for reference only. Remote mode does NOT use this. Some other E16 SysEx messages (system info, BLE, firmware update) use nibblization instead.

### The Concept

Each data byte is split into two 4-bit nibbles, sent as two separate bytes (high nibble first):

```
Data byte:  0xA3

Wire bytes: 0x0A  0x03
            high  low
```

Every data byte becomes 2 wire bytes (100% overhead). Simple but doubles the message size.

### Encoding

```
For each data byte:
    wire_byte_1 = (data_byte >> 4) & 0x0F    // high nibble
    wire_byte_2 = data_byte & 0x0F            // low nibble
```

### Decoding

```
For each pair of wire bytes:
    data_byte = (wire_byte_1 << 4) | wire_byte_2
```

---

## Putting It All Together: Building a Complete Remote Mode Message

### Message Structure

```
F0  00 21 5B  02 01  06  [MSG_ID]  [7-bit packed payload]  F7
^   ^-------^  ^---^  ^   ^         ^                        ^
|   Mfr ID     Prod   Cat  Msg      Encoded data             End
|   (OXI)      (E16)  (Remote)
```

### Full Example: Set LED (encoder 3, LED 7, color R=127 G=0 B=64)

**Step 1: Raw payload data (5 bytes):**
```
0x03  0x07  0x7F  0x00  0x40
enc   led   R     G     B
```

**Step 2: 7-bit pack the payload:**

All values are < 0x80, so the top-bits byte is `0x00`:
```
top_bits = 0x00
stripped = 0x03 0x07 0x7F 0x00 0x40
```

**Step 3: Assemble the full SysEx message:**
```
F0 00 21 5B 02 01 06 01 00 03 07 7F 00 40 F7
^  ^-----------^  ^  ^  ^  ^-----------^  ^
|  Header         |  |  |  Packed data    End
|                 |  |  Top-bits byte
|                 |  MSG_REMOTE_LED (0x01)
|                 MSG_CAT_REMOTE (0x06)
SysEx Start
```

### Full Example: OLED Labels

**Step 1: Build 80-byte raw payload:**
```
Title (16 bytes):  "My Plugin       "  (pad with spaces to 16 chars)
Label 0 (4 bytes): "Vol "
Label 1 (4 bytes): "Pan "
... (repeat for all 16 labels)
Label 15 (4 bytes): "    "
```

**Step 2: 7-bit pack all 80 bytes:**

Since ASCII text is always < 0x80, all top-bits bytes will be `0x00`. But you still must include them.

```
Group 1:  [0x00] [byte0..byte6]     (top_bits=0, 7 ASCII chars)
Group 2:  [0x00] [byte7..byte13]
...
Group 12: [0x00] [byte77..byte79]   (last group: 3 bytes + top_bits)
```

80 data bytes → 80 + 12 = 92 wire bytes for the payload.

**Step 3: Assemble:**
```
F0 00 21 5B 02 01 06 03 [92 packed bytes] F7
```

### Full Example: Enter Remote Mode (No Payload)

No encoding needed — there is no payload:
```
F0 00 21 5B 02 01 06 55 F7
```

### Full Example: Multiple LEDs in One Message

Two LEDs: (enc=0, led=0, R=127 G=0 B=0) and (enc=4, led=7, R=0 G=127 B=0)

**Raw payload (10 bytes):**
```
0x00 0x00 0x7F 0x00 0x00   0x04 0x07 0x00 0x7F 0x00
^--- LED 1 chunk ---^      ^--- LED 2 chunk ---^
```

**7-bit pack (10 bytes → group of 7 + group of 3):**

Group 1 (bytes 0-6): all < 0x80, top_bits = `0x00`
```
0x00  0x00 0x00 0x7F 0x00 0x00 0x04 0x07
```

Group 2 (bytes 7-9): all < 0x80, top_bits = `0x00`
```
0x00  0x00 0x7F 0x00
```

**Full message:**
```
F0 00 21 5B 02 01 06 01 00 00 00 7F 00 00 04 07 00 00 7F 00 F7
```

Note: the grouping is over the **entire concatenated payload**, NOT per LED chunk. The 7-byte groups do NOT align with the 5-byte LED chunks. This is critical to get right.

---

## Common Mistakes

1. **Forgetting the top-bits byte.** Every group of up to 7 data bytes MUST be preceded by a top-bits byte, even if it's `0x00`.

2. **Aligning groups to chunk boundaries.** The 7-byte packing groups are a continuous stream over the entire payload. They do NOT reset at each logical chunk (e.g., each 5-byte LED entry). A 10-byte payload = group of 7 + group of 3, NOT two groups of 5.

3. **Getting bit order wrong in the top-bits byte.** Bit 0 = MSB of the 1st data byte in the group. Bit 6 = MSB of the 7th data byte. NOT the other way around.

4. **Sending bytes >= 0x80 in SysEx.** If any byte between `F0` and `F7` is >= `0x80`, the MIDI device will either ignore the message or behave unpredictably. This is why encoding exists.

5. **Confusing the two schemes.** Remote mode uses 7-bit packing, not nibblization. If you use the wrong scheme, the device will decode garbage.

6. **Forgetting the category byte `0x06`.** Even in remote mode, every message still needs `0x06` before the message ID.

---

## Reference: Pseudocode

### Encode (for sending Host → Device)

```python
def sysex_7bit_encode(data: bytes) -> bytes:
    """Encode raw data bytes into 7-bit packed SysEx payload."""
    output = bytearray()
    i = 0
    while i < len(data):
        group = data[i : i + 7]
        top_bits = 0
        stripped = bytearray()
        for bit_pos, byte_val in enumerate(group):
            if byte_val & 0x80:
                top_bits |= (1 << bit_pos)
            stripped.append(byte_val & 0x7F)
        output.append(top_bits)
        output.extend(stripped)
        i += 7
    return bytes(output)
```

### Decode (for receiving Device → Host, or for testing)

```python
def sysex_7bit_decode(wire: bytes) -> bytes:
    """Decode 7-bit packed SysEx payload back to raw data bytes."""
    output = bytearray()
    i = 0
    while i < len(wire):
        top_bits = wire[i]
        i += 1
        for bit_pos in range(7):
            if i >= len(wire):
                break
            msb = (top_bits >> bit_pos) & 1
            output.append(wire[i] | (msb << 7))
            i += 1
    return bytes(output)
```

### Build a Complete SysEx Message

```python
SYSEX_HEADER = bytes([0xF0, 0x00, 0x21, 0x5B, 0x02, 0x01])
MSG_CAT_REMOTE = 0x06

def build_remote_sysex(msg_id: int, raw_payload: bytes = b"") -> bytes:
    """Build a complete remote mode SysEx message."""
    msg = bytearray(SYSEX_HEADER)
    msg.append(MSG_CAT_REMOTE)
    msg.append(msg_id)
    if raw_payload:
        msg.extend(sysex_7bit_encode(raw_payload))
    msg.append(0xF7)
    return bytes(msg)

# Examples:
enter_msg = build_remote_sysex(0x55)
# F0 00 21 5B 02 01 06 55 F7

exit_msg = build_remote_sysex(0x00)
# F0 00 21 5B 02 01 06 00 F7

led_msg = build_remote_sysex(0x01, bytes([3, 7, 127, 0, 64]))
# F0 00 21 5B 02 01 06 01 00 03 07 7F 00 40 F7

# LED Ring: encoder 0, red, 50% (8192 = 0x40 << 7 | 0x00), normal arc
ring_msg = build_remote_sysex(0x04, bytes([0, 127, 0, 0, 0x40, 0x00, 0]))
# F0 00 21 5B 02 01 06 04 00 00 7F 00 00 40 00 00 F7
```

---

## Test Checklist

When writing tests, verify at minimum:

- [ ] Encoding then decoding a payload returns the original bytes (round-trip)
- [ ] All encoded bytes are < `0x80`
- [ ] Empty payload → no packed bytes (just header + msg_id + F7)
- [ ] Payload of exactly 7 bytes → 1 top-bits byte + 7 data bytes = 8 wire bytes
- [ ] Payload of 8 bytes → 2 groups: (1+7) + (1+1) = 10 wire bytes
- [ ] Payload of 14 bytes → 2 groups: (1+7) + (1+7) = 16 wire bytes
- [ ] Payload with all bytes < 0x80 → all top-bits bytes are 0x00
- [ ] Payload with all bytes = 0xFF → top-bits bytes are 0x7F, all data bytes are 0x7F
- [ ] LED message with multiple 5-byte chunks: verify packing groups span across chunk boundaries
- [ ] LED Ring message with 7-byte chunks: verify packing (7 data bytes = exactly 1 group = 8 wire bytes)
- [ ] OLED labels (80 bytes of ASCII): verify correct total wire length (92 bytes)
- [ ] OLED framebuffer (1024 bytes): verify correct total wire length (1024 + ceil(1024/7) = 1024 + 147 = 1171 bytes)
