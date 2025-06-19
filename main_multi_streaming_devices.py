import usb.device
from usb.device.midi_multi import MidiMulti
import time

NUM_PORTS = 3  # e.g., 3 ports

class MyMidiMulti(MidiMulti):
    def on_open(self):
        super().on_open()
        print('Device opened by host')
    def setup_callbacks(self):
        for i in range(self.num_ports):
            self.set_in_callback(i, self._print_midi_in)
    def _print_midi_in(self, cable, cin, byte_0, byte_1, byte_2):
        command = byte_0 & 0xF0
        channel = byte_0 & 0x0F
        if command == 0x90 and byte_2 != 0:  # Note On
            print(f'RX Note On (port) ch{channel} note {byte_1} vel {byte_2}')
        elif command == 0x80 or (command == 0x90 and byte_2 == 0):  # Note Off
            print(f'RX Note Off (port) ch{channel} note {byte_1} vel {byte_2}')
        elif command == 0xB0:  # Control Change
            print(f'RX CC (port) ch{channel} ctrl {byte_1} value {byte_2}')
        else:
            print(f'RX MIDI (port): {byte_0}, {byte_1}, {byte_2}')

time.sleep_ms(1000)

m = MyMidiMulti(num_ports=NUM_PORTS)
m.setup_callbacks()
usb.device.get().init(m, builtin_driver=False, manufacturer_str="TestMaker", product_str="TestMIDI", serial_str="123456",
                    #   device_class=0xEF, device_subclass=0x02, device_protocol=0x01
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
    for port in range(NUM_PORTS):
        time.sleep(1)
        print(f'TX Note On PORT{port} ch{CHANNEL} pitch {PITCH}')
        m.note_on(port, CHANNEL, PITCH, 100)
        time.sleep(0.5)
        print(f'TX Note Off PORT{port} ch{CHANNEL} pitch {PITCH}')
        m.note_off(port, CHANNEL, PITCH, 0)
        time.sleep(1)
        print(f'TX Control PORT{port} ch{CHANNEL} ctrl 64 value {control_val}')
        m.control_change(port, CHANNEL, CONTROLLER, control_val)
        control_val = (control_val + 1) & 0x7F
        time.sleep(1)

print('USB host has reset device, example done.')