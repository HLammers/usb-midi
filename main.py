''' Example for multi-port USB MIDI 1.0 library for MicroPython based on multiple Endpoints approach

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
from usb.device.midi_multi_endpoint import MidiMulti
import time

_NUM_IN         = const(1) # Set up 3 MIDI in ports
_NUM_OUT        = const(1) # and 2 MIDI out ports
_IN_PORT_NAMES  = ('IN A', 'IN B', 'IN C') # Port names need to be longer than one character
_OUT_PORT_NAMES = ('OUT A', 'OUT B') # Port names need to be longer than one character
_MANUFACTURER = 'TestMaker'
_PRODUCT      = 'TestMIDI'
_SERIAL       = '123456'

class MidiExample(MidiMulti):

    def on_open(self):
        super().on_open()
        print('Device opened by host')

    def setup_callbacks(self):
        '''Assign callback functions to each MIDI port (Endpoint)'''
        _set_in_callback = self.set_in_callback
        _print_midi_in = self._print_midi_in
        for i in range(self.num_in):
            _set_in_callback(i, _print_midi_in)

    def _print_midi_in(self, cable, cin, byte_0, byte_1, byte_2):
        '''Example callback function which is called each time a MIDI message is received'''
        command = byte_0 & 0xF0
        channel = byte_0 & 0x0F
        if command == 0x90 and byte_2 != 0: # Note On
            print(f'RX Note On on port {cable}: channel {channel} note {byte_1} velocity {byte_2}')
        elif command == 0x80 or (command == 0x90 and byte_2 == 0): # Note Off
            print(f'RX Note Off on port {cable}: channel {channel} note {byte_1} velocity {byte_2}')
        elif command == 0xB0: # Control Change
            print(f'RX CC on port {cable}: channel {channel} ctrl {byte_1} value {byte_2}')
        else:
            print(f'RX MIDI message on port {cable}: {byte_0}, {byte_1}, {byte_2}')

# For when using VSCode: delay to allow the REPL to connect before main.py is ran
time.sleep_ms(1000)
m = MidiExample(_NUM_IN, _NUM_OUT, _IN_PORT_NAMES, _OUT_PORT_NAMES)
m.setup_callbacks()
# Remove builtin_driver=True or set it to False if you don’t want the MicroPython serial REPL available; manufacturer_str, product_str and
# serial_str are optional (builtin_driver=True doesn’t work with Windows)
# device_class=0xEF, device_subclass=2, device_protocol=1 are required because builtin_driver=True adds an IAD - without builtin_driver=True it
# isn’t needed
usb.device.get().init(m, builtin_driver=False, manufacturer_str=_MANUFACTURER, product_str=_PRODUCT, serial_str=_SERIAL,
                    #   device_class=0xEF, device_subclass=2, device_protocol=1)
)
print('Waiting for USB host to configure the interface...')
while not m.is_open():
    time.sleep_ms(100)
print('Starting MIDI loop...')
_CHANNEL = const(0)
_NOTE = const(60)
_CONTROLLER = const(64)
while m.is_open():
    for port in range(_NUM_OUT):
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