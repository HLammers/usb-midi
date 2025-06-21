import usb.device
from usb.device.midi_multi import Midi20Multi
import time

NUM_GROUPS = 3

class MyMidi20Multi(Midi20Multi):
    def on_open(self):
        super().on_open()
        print('Device opened by host')
    def setup_callbacks(self):
        for i in range(self.num_groups):
            self.set_in_callback(i, self._print_ump_in)
    def _print_ump_in(self, ump_bytes):
        print("RX UMP:", list(ump_bytes))

time.sleep_ms(1000)

group_names = ["DIN", "USB Host", "Virtual"]
m = MyMidi20Multi(num_groups=NUM_GROUPS, group_names=group_names)
m.setup_callbacks()
usb.device.get().init(
    m, builtin_driver=False,
    manufacturer_str="TestMaker",
    product_str="TestMIDI2",
    serial_str="123456",
    device_class=0xEF, device_subclass=0x02, device_protocol=0x01
)

print('Waiting for USB host to configure the interface...')
while not m.is_open():
    time.sleep_ms(100)

print('Starting MIDI 2.0 UMP loop...')

while m.is_open():
    for group in range(NUM_GROUPS):
        # Example: send a MIDI 2.0 UMP Note On message (see spec for full encoding!)
        # Here we send a dummy 4-byte UMP: [0x40 | group, ...]
        ump = bytes([0x40 | group, 0x90, 60, 127])  # Basic UMP for "Note On"
        m.send_ump(group, ump)
        print(f"Sent UMP to group {group}: {list(ump)}")
        time.sleep(1)

print('USB host has reset device, example done.')