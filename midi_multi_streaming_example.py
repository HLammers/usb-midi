''' Example for multi-port USB MIDI 1.0 library for MicroPython based on a multiple streaming interface approach

    This example demonstrates creating a custom MIDI device with 3 ports

    To run this example:

    1. Make sure usb-device-midi is installed via: mpremote mip install usb-device-midi

    2. Run the example via: mpremote run midi_example.py

    3. mpremote will exit with an error after the previous step, because when the example runs the existing USB device disconnects and then
       re-enumerates with the MIDI interface present - at this point, the example is running

    4. To see output from the example, re-connect: mpremote connect PORTNAME

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

import usb.device
from usb.device.midi_multi_streaming import MidiMulti
import time

_NUM_PORTS    = const(3) # Set up 3 pairs of MIDI in and MIDI out ports
_PORT_NAMES   = ('DIN', 'USB Host', 'Virtual')
_MANUFACTURER = 'TestMaker'
_PRODUCT      = 'TestMIDI'
_SERIAL       = '123456'

class MIDIExample(MidiMulti):

    def on_open(self):
        super().on_open()
        print('Device opened by host')

    def setup_callbacks(self):
        '''Assign callback functions to each MIDI port'''
        for i in range(self.num_ports):
            self.set_in_callback(i, self._print_midi_in)

    def _print_midi_in(self, port, cin, byte_0, byte_1, byte_2):
        '''Example callback function which is called each time a MIDI message is received'''
        command = byte_0 & 0xF0
        channel = byte_0 & 0x0F
        if command == 0x90 and byte_2 != 0: # Note On
            print(f'RX Note On on port {port}: channel {channel} note {byte_1} velocity {byte_2}')
        elif command == 0x80 or (command == 0x90 and byte_2 == 0): # Note Off
            print(f'RX Note Off on port {port}: channel {channel} note {byte_1} velocity {byte_2}')
        elif command == 0xB0: # Control Change
            print(f'RX CC on port {port}: channel {channel} ctrl {byte_1} value {byte_2}')
        else:
            print(f'RX MIDI message on port {port}: {byte_0}, {byte_1}, {byte_2}')

# For when using VSCode: delay to allow the REPL to connect before main.py is ran
time.sleep_ms(1000)
m = MIDIExample(_NUM_PORTS, _PORT_NAMES)
m.setup_callbacks()
# Remove builtin_driver=True if you don't want the MicroPython serial REPL available; manufacturer_str, product_str and serial_str are optional
usb.device.get().init(m, builtin_driver=False, manufacturer_str=_MANUFACTURER, product_str=_PRODUCT, serial_str=_SERIAL,
######
                      # device_class=0xEF, device_subclass=0x02, device_protocol=0x01
                      )
print('Waiting for USB host to configure the interface...')
while not m.is_open():
    time.sleep_ms(100)
print('Starting MIDI loop...')
_CHANNEL = const(0)
_NOTE = const(60)
_CONTROLLER = const(64)
control_val = 0
while m.is_open():
    for port in range(_NUM_PORTS):
        time.sleep(1)
        print(f'TX Note On on port {port}: channel {_CHANNEL} note {_NOTE}')
        m.note_on(port, _CHANNEL, _NOTE) # Velocity is an optional third argument
        time.sleep(0.5)
        print(f'TX Note Off on port {port}: channel {_CHANNEL} note {_NOTE}')
        m.note_off(port, _CHANNEL, _NOTE)
        time.sleep(1)
        print(f'TX CC on port {port}: channel {_CHANNEL} ctrl {_CONTROLLER} value {control_val}')
        m.control_change(port, _CHANNEL, _CONTROLLER, control_val)
        control_val = (control_val + 1) & 0x7F
        time.sleep(1)
print('USB host has reset device, example done')