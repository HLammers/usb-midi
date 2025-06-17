from micropython import const
from usb.device.core import Interface, Buffer

# USB MIDI 1.0 constants
_INTERFACE_CLASS_AUDIO = const(0x01)
_INTERFACE_SUBCLASS_AUDIO_CONTROL = const(0x01)
_INTERFACE_SUBCLASS_AUDIO_MIDISTREAMING = const(0x03)
_STD_DESC_AUDIO_ENDPOINT_LEN = const(9)
_CLASS_DESC_ENDPOINT_LEN = const(5)
_JACK_TYPE_EMBEDDED = const(0x01)
_JACK_TYPE_EXTERNAL = const(0x02)
_JACK_IN_DESC_LEN = const(6)
_JACK_OUT_DESC_LEN = const(9)
_EP_MIDI_PACKET_SIZE = 64  # Large enough for a few MIDI packets

_EP_OUT_ADDR = const(0x01)  # OUT endpoint (host->device)
_EP_IN_ADDR  = const(0x81)  # IN endpoint (device->host)

_MAX_CABLES = const(16)  # USB MIDI 1.0: up to 16 cables per endpoint

class MidiMulti(Interface):
    '''
    USB MIDI 1.0 device class supporting up to 16 virtual MIDI IN and OUT cables,
    using a single pair of endpoints and the cable number field.
    '''
    def __init__(self, num_in=1, num_out=1):
        assert 1 <= num_in <= _MAX_CABLES
        assert 1 <= num_out <= _MAX_CABLES
        self.num_in = num_in   # Number of virtual IN cables (host->device)
        self.num_out = num_out # Number of virtual OUT cables (device->host)
        self._usb_device = None
        self.in_callbacks = [None] * self.num_in
        self.rx_buffer = None
        self.tx_buffer = None
        self._open = False

    def desc_cfg(self, desc, itf_num, ep_num, strs):
        # AudioControl interface
        desc.interface(itf_num, 0, _INTERFACE_CLASS_AUDIO, _INTERFACE_SUBCLASS_AUDIO_CONTROL)
        desc.pack('<BBBHHBB', 9, 0x24, 0x01, 0x0100, 0x0009, 1, itf_num + 1)
        # MIDIStreaming interface
        ms_if_num = itf_num + 1
        desc.interface(ms_if_num, 2, _INTERFACE_CLASS_AUDIO, _INTERFACE_SUBCLASS_AUDIO_MIDISTREAMING)
        # Class-specific MIDIStreaming Interface header
        total_class_specific_len = (
            7 + self.num_in * _JACK_IN_DESC_LEN + self.num_out * _JACK_OUT_DESC_LEN
            + self.num_out * _JACK_IN_DESC_LEN + self.num_in * _JACK_OUT_DESC_LEN
        )
        desc.pack('<BBBHH', 7, 0x24, 0x01, 0x0100, total_class_specific_len)
        # IN Jacks for each virtual IN cable
        for i in range(self.num_in):
            desc.pack('<BBBBBB', _JACK_IN_DESC_LEN, 0x24, 0x02, _JACK_TYPE_EMBEDDED, 1 + i, 0x00)
        # OUT Jacks for each virtual OUT cable
        for i in range(self.num_out):
            desc.pack('<BBBBBBBBB', _JACK_OUT_DESC_LEN, 0x24, 0x03, _JACK_TYPE_EMBEDDED, 1 + self.num_in + i, 0x01, 1 + i, 1, 0x00)
        # External OUT jacks for each virtual IN cable
        for i in range(self.num_out):
            desc.pack('<BBBBBBBBB', _JACK_OUT_DESC_LEN, 0x24, 0x03, _JACK_TYPE_EXTERNAL, 1 + self.num_in + self.num_out + i, 0x01, 1 + i, 1, 0x00)
        # External IN jacks for each virtual OUT cable
        for i in range(self.num_in):
            desc.pack('<BBBBBB', _JACK_IN_DESC_LEN, 0x24, 0x02, _JACK_TYPE_EXTERNAL, 1 + self.num_in + 2*self.num_out + i, 0x00)
        # Shared OUT endpoint (host->device)
        desc.pack('<BBBBHBBB', _STD_DESC_AUDIO_ENDPOINT_LEN, 0x05, _EP_OUT_ADDR, 2, _EP_MIDI_PACKET_SIZE, 0, 0, 0)
        desc.pack('<BBBBB', _CLASS_DESC_ENDPOINT_LEN, 0x25, 0x01, self.num_in, *[1 + i for i in range(self.num_in)])
        # Shared IN endpoint (device->host)
        desc.pack('<BBBBHBBB', _STD_DESC_AUDIO_ENDPOINT_LEN, 0x05, _EP_IN_ADDR, 2, _EP_MIDI_PACKET_SIZE, 0, 0, 0)
        desc.pack('<BBBBB', _CLASS_DESC_ENDPOINT_LEN, 0x25, 0x01, self.num_out, *[1 + self.num_in + i for i in range(self.num_out)])

    def num_itfs(self):
        return 2

    def num_eps(self):
        return 2

    def on_open(self, usb_device):
        '''Called by USB stack when the device is configured and ready.'''
        self._usb_device = usb_device
        self._open = True
        self.rx_buffer = Buffer(_EP_MIDI_PACKET_SIZE)
        self.tx_buffer = Buffer(_EP_MIDI_PACKET_SIZE)
        usb_device.read_start(_EP_OUT_ADDR, self.rx_buffer, self._on_midi_in)
        if hasattr(self, 'on_midi_open'):
            self.on_midi_open()

    def set_in_callback(self, cable_number, callback):
        '''Register a callback for received MIDI messages on a virtual cable.'''
        if 0 <= cable_number < self.num_in:
            self.in_callbacks[cable_number] = callback

    def _on_midi_in(self, ep, buf):
        if not self._open:
            return
        data = bytes(buf)
        for ofs in range(0, len(data) - 3, 4):
            pkt = data[ofs:ofs+4]
            cable_number = (pkt[0] >> 4) & 0x0F
            if 0 <= cable_number < self.num_in:
                cb = self.in_callbacks[cable_number]
                if cb:
                    cb(pkt, cable_number)
        # Restart read
        self._usb_device.read_start(_EP_OUT_ADDR, buf, self._on_midi_in)

    def send_event(self, cable_number, msg_bytes):
        '''Send a MIDI message on a device->host port (OUT port to host).

        Args:
            cable_number (int): Index of OUT virtual cable (0-based)
            msg_bytes (bytes or list of int): 3- or 4-byte USB MIDI Event Packet (excluding cable/CIN)
        '''
        if not self._open or not self._usb_device:
            raise RuntimeError('USB device not open')
        if not (0 <= cable_number < self.num_out):
            raise ValueError('Invalid OUT cable number')
        # Compose USB MIDI Event Packet
        # msg_bytes may already be 4 bytes with correct cable/CIN header,
        # or 3 bytes just for the MIDI message.
        if len(msg_bytes) == 4:
            packet = bytearray(msg_bytes)
            packet[0] = ((cable_number & 0x0F) << 4) | (msg_bytes[0] & 0x0F)
        elif len(msg_bytes) == 3:
            # MIDI message: add header byte
            status = msg_bytes[0]
            if status >= 0xF8:
                cin = 0x0F
            elif status >= 0xF0:
                cin = 0x05
            elif status >= 0xC0 and status < 0xE0:
                cin = 0x02
            elif status >= 0x80 and status < 0xF0:
                cin = (status >> 4) & 0x0F
            else:
                cin = 0x00
            packet = bytearray(4)
            packet[0] = ((cable_number & 0x0F) << 4) | cin
            packet[1:4] = bytes(msg_bytes)
        else:
            raise ValueError('msg_bytes must be 3 or 4 bytes')
        self._usb_device.write(_EP_IN_ADDR, packet)

    def send_note_on(self, cable_number, channel, note, velocity):
        '''Send a Note On message on a given OUT virtual cable.'''
        midi_status = 0x90 | (channel & 0x0F)
        self.send_event(cable_number, [midi_status, note & 0x7F, velocity & 0x7F])

    def send_note_off(self, cable_number, channel, note, velocity=0):
        '''Send a Note Off message on a given OUT virtual cable.'''
        midi_status = 0x80 | (channel & 0x0F)
        self.send_event(cable_number, [midi_status, note & 0x7F, velocity & 0x7F])