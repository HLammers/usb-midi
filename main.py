# MicroPython USB MIDI Multi-Cable example (asymmetric ports)
#
# Demonstrates using the MidiMulti class to create a MIDI device with
# any number of IN (host->device) and OUT (device->host) virtual cables.
# On Raspberry Pi Pico 2, blinks the on-board LED when MIDI data is received.
#
# To run:
#   1. Ensure your custom MidiMulti module is available in your PYTHONPATH.
#   2. mpremote run midi_multi_example.py
#   3. After device re-enumeration, reconnect: mpremote connect PORTNAME
#
# Copyright (c) 2025, your contributors. MIT License.

import usb.device
from usb.device.midi_multi import MidiMulti
import time

time.sleep_ms(1000)

try:
    from machine import Pin, Timer
    LED_PIN = 25  # Pico 2 onboard LED
    led = Pin(LED_PIN, Pin.OUT)
    led_timer = Timer()
    def blink_led(ms=50):
        led.value(1)
        led_timer.init(mode=Timer.ONE_SHOT, period=ms, callback=lambda t: led.value(0))
except Exception:
    # Fallback for environments without machine/Pin/Timer
    def blink_led(ms=50):
        pass

# Example: 3 MIDI IN ports (host->device), 2 MIDI OUT ports (device->host)
NUM_IN = 3
NUM_OUT = 2

class MyMidiMulti(MidiMulti):
    def on_open(self, usb_dev):
        super().on_open(usb_dev)
        print("Device opened by host")

    # Example: print received MIDI data for each IN port and blink LED
    def setup_callbacks(self):
        for i in range(self.num_in):
            self.set_in_callback(i, self._print_midi_in)

    def _print_midi_in(self, msg_bytes, cable_number):
        blink_led()
        # msg_bytes: always 4 bytes (USB MIDI event packet)
        # For note on/off, decode status
        status = msg_bytes[1] & 0xF0
        chan = msg_bytes[1] & 0x0F
        if status == 0x90 and msg_bytes[3] != 0:  # Note On
            print(f"RX Note On (cable {cable_number}) ch{chan} note {msg_bytes[2]} vel {msg_bytes[3]}")
        elif status == 0x80 or (status == 0x90 and msg_bytes[3] == 0):  # Note Off
            print(f"RX Note Off (cable {cable_number}) ch{chan} note {msg_bytes[2]} vel {msg_bytes[3]}")
        elif status == 0xB0:  # Control Change
            print(f"RX CC (cable {cable_number}) ch{chan} ctrl {msg_bytes[2]} value {msg_bytes[3]}")
        else:
            print(f"RX MIDI (cable {cable_number}): {list(msg_bytes)}")

m = MyMidiMulti(num_in=NUM_IN, num_out=NUM_OUT)
usb.device.get().init(m, builtin_driver=True)
print("Waiting for USB host to configure the interface...")

while not m._open:
    time.sleep_ms(100)
m.setup_callbacks()

print("Starting MIDI multi-port loop...")

# Example: Send Note On/Off on all OUT ports, round-robin
CHANNEL = 0
PITCH = 60
control_val = 0
OUT_PORTS = NUM_OUT

while m._open:
    for out_port in range(OUT_PORTS):
        print(f"TX Note On OUT{out_port} ch{CHANNEL} pitch {PITCH}")
        m.send_note_on(out_port, CHANNEL, PITCH, 100)
        time.sleep(0.5)
        print(f"TX Note Off OUT{out_port} ch{CHANNEL} pitch {PITCH}")
        m.send_note_off(out_port, CHANNEL, PITCH, 0)
        time.sleep(0.5)
        print(f"TX Control OUT{out_port} ch{CHANNEL} ctrl 64 value {control_val}")
        m.send_midi(out_port, [((out_port & 0x0F) << 4) | 0xB, 0xB0 | CHANNEL, 64, control_val & 0x7F])
        time.sleep(0.5)
        control_val = (control_val + 1) & 0x7F

print("USB host has reset device, example done.")