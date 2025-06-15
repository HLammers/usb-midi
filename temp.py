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
_EP_MIDI_PACKET_SIZE = 64  # Larger buffer for higher bandwidth
_EP_BULK = const(0x02)

def midi_ep_out(n): return 0x01 + n  # OUT endpoints: 0x01, 0x02, ...
def midi_ep_in(n):  return 0x81 + n  # IN endpoints: 0x81, 0x82, ...

class MidiMulti(Interface):
    '''
    USB MIDI 1.0 device class supporting arbitrary asymmetric number of input and output virtual cables.
    Each IN cable corresponds to a separate OUT endpoint (host->device).
    Each OUT cable corresponds to a separate IN endpoint (device->host).
    '''
    def __init__(self, num_in=1, num_out=1, ac_interface_name='MIDI Control'):
        self.num_in = num_in
        self.num_out = num_out
        self.ac_interface_name = ac_interface_name
        self._usb_device = None
        self.in_callbacks = [None] * self.num_in
        self.rx_buffers = [None] * self.num_in
        self.tx_buffers = [None] * self.num_out
        self._open = False

    def desc_cfg(self, desc, itf_num, ep_num, strs):
        # AudioControl interface (interface 0)
        ac_if_str_idx = self._get_str_idx(strs, self.ac_interface_name)
        desc.interface(itf_num, 0, _INTERFACE_CLASS_AUDIO, _INTERFACE_SUBCLASS_AUDIO_CONTROL, ac_if_str_idx)
        desc.pack('<BBBHHBB', 9, 0x24, 0x01, 0x0100, 0x0009, 1, itf_num + 1)

        # MIDIStreaming Interface (interface 1)
        ms_if_num = itf_num + 1
        num_eps = self.num_in + self.num_out
        desc.interface(ms_if_num, num_eps, _INTERFACE_CLASS_AUDIO, _INTERFACE_SUBCLASS_AUDIO_MIDISTREAMING)
        # Class-specific MS Interface header
        total_class_len = (
            7 + self.num_in * _JACK_IN_DESC_LEN + self.num_out * _JACK_OUT_DESC_LEN
            + self.num_out * _JACK_IN_DESC_LEN + self.num_in * _JACK_OUT_DESC_LEN
        )
        desc.pack('<BBBHH', 7, 0x24, 0x01, 0x0100, total_class_len)

        # IN Jacks for each IN cable (host->device)
        for i in range(self.num_in):
            emb_in_id = 1 + i
            _jack_in_desc(desc, _JACK_TYPE_EMBEDDED, emb_in_id)
        # OUT Jacks for each OUT cable (device->host)
        for i in range(self.num_out):
            emb_out_id = 1 + self.num_in + i
            _jack_out_desc(desc, _JACK_TYPE_EMBEDDED, emb_out_id, 1 + i, 1)

        # External OUT jacks for IN cables (host->device)
        for i in range(self.num_out):
            ext_out_id = 1 + self.num_in + self.num_out + i
            _jack_out_desc(desc, _JACK_TYPE_EXTERNAL, ext_out_id, 1 + i, 1)
        # External IN jacks for OUT cables (device->host)
        for i in range(self.num_in):
            ext_in_id = 1 + self.num_in + 2 * self.num_out + i
            _jack_in_desc(desc, _JACK_TYPE_EXTERNAL, ext_in_id)

        # Endpoints for IN cables (host->device)
        for i in range(self.num_in):
            emb_in_id = 1 + i
            _audio_endpoint(desc, midi_ep_out(i))
            _midi_class_ep(desc, emb_in_id)

        # Endpoints for OUT cables (device->host)
        for i in range(self.num_out):
            emb_out_id = 1 + self.num_in + i
            _audio_endpoint(desc, midi_ep_in(i))
            _midi_class_ep(desc, emb_out_id)

    def num_itfs(self): return 2
    def num_eps(self): return self.num_in + self.num_out

    def _get_str_idx(self, strs, s):
        if not s:
            return 0
        if s in strs:
            return strs.index(s) + 1
        strs.append(s)
        return len(strs)

    # ---- Buffer and I/O setup ----

    def on_open(self, usb_device):
        '''Called by USB stack when the device is configured and ready.'''
        self._usb_device = usb_device
        self._open = True

        # Allocate RX and TX Buffers for each endpoint
        for i in range(self.num_in):
            self.rx_buffers[i] = Buffer(_EP_MIDI_PACKET_SIZE)
            usb_device.read_start(midi_ep_out(i), self.rx_buffers[i], lambda ep, buf, i=i: self._on_midi_in(i, ep, buf))
        for i in range(self.num_out):
            self.tx_buffers[i] = Buffer(_EP_MIDI_PACKET_SIZE)
        # Custom: user callback for on_open
        if hasattr(self, 'on_midi_open'):
            self.on_midi_open()

    # ---- MIDI message receiving ----
    def set_in_callback(self, cable_number, callback):
        '''Register a callback for received MIDI messages on a host->device cable.

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
        self._usb_device.read_start(midi_ep_out(cable_number), buf, lambda ep, buf, i=cable_number: self._on_midi_in(i, ep, buf))

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
        self._usb_device.write(midi_ep_in(cable_number), packet)

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

# ---- Descriptor helpers ----

def _jack_in_desc(desc, bJackType, bJackID):
    desc.pack('<BBBBBB', _JACK_IN_DESC_LEN, 0x24, 0x02, bJackType, bJackID, 0x00)

def _jack_out_desc(desc, bJackType, bJackID, bSourceId, bSourcePin):
    desc.pack('<BBBBBBBBB', _JACK_OUT_DESC_LEN, 0x24, 0x03, bJackType, bJackID, 0x01, bSourceId, bSourcePin, 0x00)

def _audio_endpoint(desc, bEndpointAddress):
    desc.pack('<BBBBHBBB', _STD_DESC_AUDIO_ENDPOINT_LEN, 0x05, bEndpointAddress, 2, _EP_MIDI_PACKET_SIZE, 0, 0, 0)

def _midi_class_ep(desc, jack_id):
    desc.pack('<BBBBB', _CLASS_DESC_ENDPOINT_LEN, 0x25, 0x01, 1, jack_id)