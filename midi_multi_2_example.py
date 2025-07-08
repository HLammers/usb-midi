''' Example for multi-port USB MIDI 2.0 library for MicroPython, accepting MIDI 1.0 Protocol messages

    This example demonstrates creating a custom MIDI device with 3 ports

    To run this example:

    1. Run the example via: mpremote run midi_multi_2_example.py

    2. mpremote will exit with an error after the previous step, because when the example runs the existing USB device disconnects and then
       re-enumerates with the MIDI interface present - at this point, the example is running

    3. To see output from the example, re-connect: mpremote connect PORTNAME

    Copyright (c) 2025 Harm Lammers
    
    Parts are taken from https://github.com/micropython/micropython-lib/blob/master/micropython/usb/examples/device/midi_example.py,
    copyright (c) 2023-2024 Angus Gratton, published under MIT licence

    MIT licence:

    Permission is hereby granted, free of charge, to any person obtaining a copy of this software and associated documentation files (the
    "Software"), to deal in the Software without restriction, including without limitation the rights to use, copy, modify, merge, publish,
    distribute, sublicense, and/or sell copies of the Software, and to permit persons to whom the Software is furnished to do so, subject to
    the following conditions:

    The above copyright notice and this permission notice shall be included in all copies or substantial portions of the Software.

    THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF
    MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY
    CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE
    SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.'''

import machine
import usb.device
from usb.device.midi_multi_2 import MidiMulti
import time

_NUM_PORTS    = const(3) # Set up 3 MIDI IN/OUT ports
_PORT_NAMES   = ['Port A', 'Port B', 'Port C'] # Port names need to be longer than one character (needs to be of type List)
_MANUFACTURER = 'TestMaker'
_PRODUCT      = 'TestMIDI'
_SERIAL       = machine.unique_id()

# Global variables
channel = 0
note = 60

class MIDIExample(MidiMulti):

    def __init__(self, num_ports=1, port_names=None):
        super().__init__(num_ports, port_names, self._print_midi_in)

    def on_open(self):
        super().on_open()
        print('Device opened by host')

    def _print_midi_in(self, ump_bytes):
        '''Example callback function which is called each time a MIDI message is received'''
        global channel, note
        group = (b_0 := ump_bytes[0]) & 0xF0
        message_type = b_0 & 0xF0
        if message_type == 0x0 and len(ump_bytes) == 4: # Utility Messages
            pass
        elif message_type == 0x1 and len(ump_bytes) == 4: # System Real Time Messages / System Common Messages
            pass
        elif message_type == 0x2 and len(ump_bytes) == 4: # MIDI 1.0 Channel Voice Messages
            command = (byte_0 := ump_bytes[1]) & 0xF0
            channel = byte_0 & 0x0F
            if command == 0x90 and ump_bytes[3] != 0: # Note On
                print(f'RX Note On on port {group}: channel {channel} note {ump_bytes[2]} velocity {ump_bytes[3]}')
                note = ump_bytes[2]
            elif command == 0x80 or (command == 0x90 and ump_bytes[3] == 0): # Note Off
                print(f'RX Note Off on port {group}: channel {channel} note {ump_bytes[2]} velocity {ump_bytes[3]}')
            elif command == 0xB0: # Control Change
                print(f'RX CC on port {group}: channel {channel} ctrl {ump_bytes[2]} value {ump_bytes[3]}')
            else:
                print(f'RX MIDI message on port {group}: {byte_0}, {ump_bytes[2]}, {ump_bytes[3]}')
        elif message_type == 0x3 and len(ump_bytes) == 8: # Data Messages (including System Exclusive)
            pass
        # elif message_type == 0x4 and len(ump_bytes) == 8: # MIDI 2.0 Channel Voice Messages (not supported in MIDI 1.0 Protocol mode)
        #     pass

# For when using VSCode: delay to allow the REPL to connect before main.py is ran
time.sleep_ms(1000)
m = MIDIExample(_NUM_PORTS, _PORT_NAMES)
# Remove builtin_driver=True or set it to False if you don’t want the MicroPython serial REPL available; manufacturer_str, product_str and
# serial_str are optional (builtin_driver=True doesn’t work with Windows)
# device_class=0xEF, device_subclass=2, device_protocol=1 are required because builtin_driver=True adds an IAD - without builtin_driver=True it
# isn’t needed
usb.device.get().init(m, builtin_driver=False, manufacturer_str=_MANUFACTURER, product_str=_PRODUCT, serial_str=_SERIAL,
                      device_class=0xEF, device_subclass=2, device_protocol=1)
print('Waiting for USB host to configure the interface...')
while not m.is_open():
    time.sleep_ms(100)
print('Starting MIDI loop...')
_CONTROLLER = const(64)
control_val = 0
while m.is_open():
    for i, port in enumerate(range(_NUM_PORTS)):
        print(f'TX Note On on port {port}: channel {channel} note {note + i}')
        m.note_on(port, channel, note + i) # Velocity is an optional third argument
        time.sleep(0.5)
        print(f'TX Note Off on port {port}: channel {channel} note {note + i}')
        m.note_off(port, channel, note + i)
        time.sleep(1)
        print(f'TX CC on port {port}: channel {channel} ctrl {_CONTROLLER} value {control_val}')
        m.control_change(port, channel, _CONTROLLER, control_val)
        control_val = (control_val + 1) & 0x7F
        time.sleep(1)
print('USB host has reset device, example done')