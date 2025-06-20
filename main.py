import usb.device
from usb.device.midi_multi import MidiMulti
import time

NUM_IN = 2
NUM_OUT = 2

class MyMidiMulti(MidiMulti):
    def on_open(self):
        super().on_open()
        print('Device opened by host')
    def setup_callbacks(self):
        for i in range(self.num_in):
            self.set_in_callback(i, self._print_midi_in)
    def _print_midi_in(self, cable, cin, byte_0, byte_1, byte_2):
        print(f'IN PORT: {cable}, CIN: {cin}, DATA: {byte_0}, {byte_1}, {byte_2}')

time.sleep_ms(1000)

m = MyMidiMulti(num_in=NUM_IN, num_out=NUM_OUT)
m.setup_callbacks()
usb.device.get().init(m, builtin_driver=False, manufacturer_str="TestMaker", product_str="TestMIDI", serial_str="123456")

print('Waiting for USB host to configure the interface...')
while not m.is_open():
    time.sleep_ms(100)
print('Starting MIDI loop...')
CHANNEL = 0
PITCH = 60
while m.is_open():
    for out_port in range(NUM_OUT):
        print(f'Sending Note On to OUT port {out_port}')
        m.note_on(out_port, CHANNEL, PITCH, 100)
        time.sleep(0.5)
        print(f'Sending Note Off to OUT port {out_port}')
        m.note_off(out_port, CHANNEL, PITCH, 0)
        time.sleep(1)

print('USB host has reset device, example done.')