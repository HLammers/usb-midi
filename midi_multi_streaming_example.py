''' Example for multi-port USB MIDI 1.0 library for MicroPython based on a multiple MIDI Streaming interface approach

    This example demonstrates creating a custom MIDI device with 3 ports

    To run this example:

    1. Run the example via: mpremote run midi_multi_streaming_example.py

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
from usb.device.midi_multi_streaming import MidiMulti
import time

_NUM_IN       = const(3) # Set up 3 MIDI IN ports
_NUM_OUT      = const(3) # and 3 MIDI OUT ports (only _NUM_IN == _NUM_OUT works for Windows)
_PORT_NAMES   = ['Port A', 'Port B', 'Port C'] # Port names need to be longer than one character (needs to be of type List)
_MANUFACTURER = 'TestMaker'
_PRODUCT      = 'TestMIDI'
_SERIAL       = machine.unique_id()

# Global variables
channel = 0
note = 60

class MIDIExample(MidiMulti):

    def __init__(self, num_in=1, num_out=1, port_names=None):
        super().__init__(num_in, num_out, port_names, self._print_midi_in)

    def on_open(self):
        super().on_open()
        print('Device opened by host')

    def _print_midi_in(self, port, data_packet):
        '''Example callback function which is called each time a MIDI message is received'''
        global channel, note
        # cin = data_packet[0] & 0x0F
        command = (byte_0 := data_packet[1]) & 0xF0
        channel = byte_0 & 0x0F
        if command == 0x90 and data_packet[3] != 0: # Note On
            print(f'RX Note On on port {port}: channel {channel} note {data_packet[2]} velocity {data_packet[3]}')
            note = data_packet[2]
        elif command == 0x80 or (command == 0x90 and data_packet[3] == 0): # Note Off
            print(f'RX Note Off on port {port}: channel {channel} note {data_packet[2]} velocity {data_packet[3]}')
        elif command == 0xB0: # Control Change
            print(f'RX CC on port {port}: channel {channel} ctrl {data_packet[2]} value {data_packet[3]}')
        else:
            print(f'RX MIDI message on port {port}: {byte_0}, {data_packet[2]}, {data_packet[3]}')

# For when using VSCode: delay to allow the REPL to connect before main.py is ran
time.sleep_ms(1000)
m = MIDIExample(_NUM_IN, _NUM_OUT, _PORT_NAMES)
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
    for i, port in enumerate(range(_NUM_OUT)):
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