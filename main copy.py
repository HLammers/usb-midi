from usb.device.core import Interface, Buffer

_INTERFACE_CLASS_AUDIO = const(0x01)
_INTERFACE_SUBCLASS_AUDIO_CONTROL = const(0x01)
_INTERFACE_SUBCLASS_AUDIO_MIDISTREAMING = const(0x03)
_STD_DESC_AUDIO_ENDPOINT_LEN = const(9)
_CLASS_DESC_ENDPOINT_LEN = const(5)
_JACK_TYPE_EMBEDDED = const(0x01)
_JACK_TYPE_EXTERNAL = const(0x02)
_JACK_IN_DESC_LEN = const(6)
_JACK_OUT_DESC_LEN = const(9)
_EP_MIDI_PACKET_SIZE = const(32)
_FIRST_OUT_ENDPOINT = const(0x01)
_FIRST_IN_ENDPOINT = const(0x81)
_EP_MIDI_PACKET_SIZE = const(64) # Larger buffer for higher bandwidth

class MidiAsymmetrical(Interface):
    '''
    USB MIDI 1.0 device class supporting arbitrary asymmetric number of input and output virtual cables.
    Each IN cable corresponds to a separate OUT endpoint (host->device).
    Each OUT cable corresponds to a separate IN endpoint (device->host).
    '''

    def __init__(self, num_in=1, num_out=1):
        self.num_in = num_in
        self.num_out = num_out
        self._usb_device = None
        self.in_callbacks = [None] * num_in
        self.rx_buffers = [None] * num_in
        self.tx_buffers = [None] * num_out
        self._open = False

    def desc_cfg(self, desc, itf_num, ep_num, strs):
        # AudioControl interface
        desc.interface(itf_num, 0, _INTERFACE_CLASS_AUDIO, _INTERFACE_SUBCLASS_AUDIO_CONTROL)
        desc.pack('<BBBHHBB', 9, 0x24, 0x01, 0x0100, 0x0009, 1, itf_num + 1)
        # MIDIStreaming interface
        ms_if_num = itf_num + 1
        num_eps = (num_in := self.num_in) + (num_out := self.num_out)
        desc.interface(ms_if_num, num_eps, _INTERFACE_CLASS_AUDIO, _INTERFACE_SUBCLASS_AUDIO_MIDISTREAMING)
        # Class-specific MIDIStreaming Interface header
        total_class_specific_len = (
            7 + self.num_in * _JACK_IN_DESC_LEN + num_out * _JACK_OUT_DESC_LEN
            + self.num_out * _JACK_IN_DESC_LEN + num_in * _JACK_OUT_DESC_LEN
        )
        desc.pack('<BBBHH', 7, 0x24, 0x01, 0x0100, total_class_specific_len)
        # IN Jacks for each virtual IN cable
        for i in range(num_in := self.num_in):
            desc.pack('<BBBBBB', _JACK_IN_DESC_LEN, 0x24, 0x02, _JACK_TYPE_EMBEDDED, 1 + i, 0x00)
        # OUT Jacks for each virtual OUT cable
        for i in range(num_out := self.num_out):
            desc.pack('<BBBBBBBBB', _JACK_OUT_DESC_LEN, 0x24, 0x03, _JACK_TYPE_EMBEDDED, 1 + num_in + i, 0x01, 1 + i, 1, 0x00)
        # External OUT jacks for each virtual IN cable
        for i in range(num_out):
            desc.pack('<BBBBBBBBB', _JACK_OUT_DESC_LEN, 0x24, 0x03, _JACK_TYPE_EXTERNAL, 1 + num_in + num_out + i, 0x01, 1 + i, 1, 0x00)
        # External IN jacks for each virtual OUT cable
        for i in range(num_in):
            desc.pack('<BBBBBB', _JACK_IN_DESC_LEN, 0x24, 0x02, _JACK_TYPE_EXTERNAL, 1 + num_in + 2 * num_out + i, 0x00)
        # Endpoints for each virtual IN cable
        for i in range(num_in):
            desc.pack('<BBBBHBBB', _STD_DESC_AUDIO_ENDPOINT_LEN, 0x05, _FIRST_OUT_ENDPOINT + i, 2, _EP_MIDI_PACKET_SIZE, 0, 0, 0)
            desc.pack('<BBBBB', _CLASS_DESC_ENDPOINT_LEN, 0x25, 0x01, 1, 1 + i)
        # Endpoints for each virtual OUT cable
        for i in range(num_out):
            desc.pack('<BBBBHBBB', _STD_DESC_AUDIO_ENDPOINT_LEN, 0x05, _FIRST_IN_ENDPOINT + i, 2, _EP_MIDI_PACKET_SIZE, 0, 0, 0)
            desc.pack('<BBBBB', _CLASS_DESC_ENDPOINT_LEN, 0x25, 0x01, 1, 1 + num_in + i)

    def num_itfs(self):
        return 2

    def num_eps(self):
        return self.num_in + self.num_out

    # ---- Buffer and I/O setup ----

    def on_open(self, usb_device):
        '''Called by USB stack when the device is configured and ready.'''
        self._usb_device = usb_device
        self._open = True

        # Allocate RX and TX Buffers for each endpoint
        for i in range(self.num_in):
            self.rx_buffers[i] = Buffer(_EP_MIDI_PACKET_SIZE)
            usb_device.read_start(0x01 + i, self.rx_buffers[i], lambda ep, buf, i=i: self._on_midi_in(i, ep, buf))
        for i in range(self.num_out):
            self.tx_buffers[i] = Buffer(_EP_MIDI_PACKET_SIZE)
        # Custom: user callback for on_open
        if hasattr(self, 'on_midi_open'):
            self.on_midi_open()

    # MIDI message handling and callbacks

    def set_in_callback(self, cable_number, callback):
        '''
        Register a callback for received MIDI messages on a host->device cable.

        Args:
            cable_number (int): Index of IN port (0-based)
            callback (function): Function taking (msg_bytes, cable_number)
        '''
        if 0 <= cable_number < self.num_in:
            self.in_callbacks[cable_number] = callback

    def _on_midi_in(self, cable_number, ep, buf):
        '''Called by USB stack when a MIDI message is received on IN endpoint.'''
        if not self._open:
            return
        data = bytes(buf)
        # MIDI event packets are 4 bytes each
        for ofs in range(0, len(data) - 3, 4):
            pkt = data[ofs:ofs+4]
            cb = self.in_callbacks[cable_number]
            if cb:
                cb(pkt, cable_number)
        # Restart read for next packet
        self._usb_device.read_start(0x01 + cable_number, buf, lambda ep, buf, i=cable_number: self._on_midi_in(i, ep, buf))


    # ---- MIDI message sending ----
    def send_midi(self, cable_number, msg_bytes):
        '''Send a MIDI message on a device->host port (OUT port to host).

        Args:
            cable_number (int): Index of OUT port (0-based)
            msg_bytes (bytes or list of int): 3- or 4-byte USB MIDI Event Packet
        '''
        if not self._open or not self._usb_device:
            raise RuntimeError('USB device not open')
        if not (0 <= cable_number < self.num_out):
            raise ValueError('Invalid OUT cable number')
        packet = bytearray(msg_bytes)
        while len(packet) < 4:
            packet.append(0)
        # Write to the correct IN endpoint for this OUT port
        self._usb_device.write(0x81 + cable_number, packet)

    def send_note_on(self, cable_number, channel, note, velocity):
        '''Send a Note On message on a given OUT port.'''
        code_index = 0x9  # Note On
        midi_status = 0x90 | (channel & 0x0F)
        packet = [
            ((cable_number & 0x0F) << 4) | code_index,
            midi_status,
            note & 0x7F,
            velocity & 0x7F
        ]
        self.send_midi(cable_number, packet)

    def send_note_off(self, cable_number, channel, note, velocity=0):
        '''Send a Note Off message on a given OUT port.'''
        code_index = 0x8
        midi_status = 0x80 | (channel & 0x0F)
        packet = [
            ((cable_number & 0x0F) << 4) | code_index,
            midi_status,
            note & 0x7F,
            velocity & 0x7F
        ]
        self.send_midi(cable_number, packet)

if __name__ == '__main__':
    import time
    import usb.device

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