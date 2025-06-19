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

_LED_PIN = 25  # Pico 2 onboard LED

# try:
#     from machine import Pin, Timer
#     led = Pin(_LED_PIN, Pin.OUT)
#     led_timer = Timer(0)
#     def blink_led(ms=50):
#         led.value(1)
#         led_timer.init(mode=Timer.ONE_SHOT, period=ms, callback=lambda t: led.value(0))
# except Exception:
#     # Fallback for environments without machine/Pin/Timer
#     def blink_led(ms=50):
#         pass

# Example: 3 MIDI IN ports (host->device), 2 MIDI OUT ports (device->host)
NUM_IN = 3
NUM_OUT = 2

class MyMidiMulti(MidiMulti):

    def on_open(self):
        super().on_open()
        print('Device opened by host')

    # Example: print received MIDI data for each IN port and blink LED
    def setup_callbacks(self):
        for i in range(self.num_in):
            self.set_in_callback(i, self._print_midi_in)

    def _print_midi_in(self, cable, cin, byte_0, byte_1, byte_2):
        # blink_led()
        command = byte_0 & 0xF0
        channel = byte_0 & 0x0F
        if command == 0x90 and byte_2 != 0:  # Note On
            print(f'RX Note On (cable {cable}) ch{channel} note {byte_1} vel {byte_2}')
        elif command == 0x80 or (command == 0x90 and byte_2 == 0):  # Note Off
            print(f'RX Note Off (cable {cable}) ch{channel} note {byte_1} vel {byte_2}')
        elif command == 0xB0:  # Control Change
            print(f'RX CC (cable {cable}) ch{channel} ctrl {byte_1} value {byte_2}')
        else:
            print(f'RX MIDI (cable {cable}): {byte_0}, {byte_1}, {byte_2}')

# Delay to allow the REPL in VSCode to connect
time.sleep_ms(1000)

m = MyMidiMulti(num_in=NUM_IN, num_out=NUM_OUT)
m.setup_callbacks()
# Remove builtin_driver=True if you don't want the MicroPython serial REPL available.
usb.device.get().init(m, builtin_driver=False, manufacturer_str="TestMaker", product_str="TestMIDI", serial_str="123456",
                      device_class=0xEF, device_subclass=0x02, device_protocol=0x01,
###### for testing pursposes only
                    #   id_vendor=0x0582, # Roland VID
                    #   id_product=0x0006 # Roland UM-1 PID
                      )

print('Waiting for USB host to configure the interface...')

while not m.is_open():
    time.sleep_ms(100)

print('Starting MIDI loop...')

CHANNEL = 0
PITCH = 60
CONTROLLER = 64

control_val = 0

while m.is_open():
    for out_port in range(NUM_OUT):
        time.sleep(1)
        print(f'TX Note On OUT{out_port} ch{CHANNEL} pitch {PITCH}')
        m.note_on(out_port, CHANNEL, PITCH, 100)
        time.sleep(0.5)
        print(f'TX Note Off OUT{out_port} ch{CHANNEL} pitch {PITCH}')
        m.note_off(out_port, CHANNEL, PITCH, 0)
        time.sleep(1)
        print(f'TX Control OUT{out_port} ch{CHANNEL} ctrl 64 value {control_val}')
        m.send_event(out_port, 0xB, 0xB0 | CHANNEL, CONTROLLER, control_val)
        control_val = (control_val + 1) & 0x7F
        time.sleep(1)

print('USB host has reset device, example done.')