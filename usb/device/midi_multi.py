from micropython import schedule
from usb.device.core import Interface, Buffer
import time

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
_EP_IN_FLAG = const(0x80)
_EP_MIDI_PACKET_SIZE = 64  # Larger buffer for higher bandwidth
_EP_BULK = const(0x02)

_MAX_CABLES = const(16)  # USB MIDI 1.0: up to 16 cables per endpoint

# MIDI Status bytes. For Channel messages these are only the upper 4 bits, ORed with the channel number.
# As per https://www.midi.org/specifications-old/item/table-1-summary-of-midi-message
_MIDI_NOTE_OFF = const(0x80)
_MIDI_NOTE_ON = const(0x90)
_MIDI_POLY_KEYPRESS = const(0xA0)
_MIDI_CONTROL_CHANGE = const(0xB0)

# USB-MIDI CINs (Code Index Numbers), as per USB MIDI Table 4-1
_CIN_SYS_COMMON_2BYTE = const(0x2)
_CIN_SYS_COMMON_3BYTE = const(0x3)
_CIN_SYSEX_START = const(0x4)
_CIN_SYSEX_END_1BYTE = const(0x5)
_CIN_SYSEX_END_2BYTE = const(0x6)
_CIN_SYSEX_END_3BYTE = const(0x7)
_CIN_NOTE_OFF = const(0x8)
_CIN_NOTE_ON = const(0x9)
_CIN_POLY_KEYPRESS = const(0xA)
_CIN_CONTROL_CHANGE = const(0xB)
_CIN_PROGRAM_CHANGE = const(0xC)
_CIN_CHANNEL_PRESSURE = const(0xD)
_CIN_PITCH_BEND = const(0xE)
_CIN_SINGLE_BYTE = const(0xF)  # Not currently supported

class MidiMulti(Interface):
    '''
    USB MIDI 1.0 device class supporting up to 16 virtual MIDI IN and OUT cables,
    using a single pair of endpoints and the cable number field.
    '''

    def __init__(self, num_in=1, num_out=1):
        if not 1 <= num_in <= _MAX_CABLES:
            raise ValueError(f'num_in ({num_in}) should be between 1 and {_MAX_CABLES}')
        if not 1 <= num_out <= _MAX_CABLES:
            raise ValueError(f'num_out ({num_out}) should be between 1 and {_MAX_CABLES}')
        super().__init__()
        self.num_in = num_in
        self.num_out = num_out
        self.ep_out = None  # Set during enumeration. RX direction (host to device)
        self.ep_in = None  # TX direction (device to host)
        self._rx_buffer = Buffer(_EP_MIDI_PACKET_SIZE)
        self._tx_buffer = Buffer(_EP_MIDI_PACKET_SIZE)
        self._in_callbacks = [None] * num_in

    def set_in_callback(self, cable_number, callback):
        '''Register a callback for received MIDI messages on a virtual cable.'''
        if 0 <= cable_number < self.num_in:
            self._in_callbacks[cable_number] = callback

    # Helper functions for sending common MIDI messages

    def note_on(self, cable, channel, pitch, vel=0x40):
        self.send_event(cable, _CIN_NOTE_ON, _MIDI_NOTE_ON | channel, pitch, vel)

    def note_off(self, cable, channel, pitch, vel=0x40):
        self.send_event(cable, _CIN_NOTE_OFF, _MIDI_NOTE_OFF | channel, pitch, vel)

    def control_change(self, cable, channel, controller, value):
        self.send_event(cable, _CIN_CONTROL_CHANGE, _MIDI_CONTROL_CHANGE | channel, controller, value)

    def send_event(self, cable, cin, midi0, midi1=0, midi2=0):
        # Queue a MIDI Event Packet to send to the host.
        #
        # CIN = USB-MIDI Code Index Number, see USB MIDI 1.0 section 4 "USB-MIDI Event Packets"
        #
        # Remaining arguments are 0-3 MIDI data bytes.
        #
        # Note this function returns when the MIDI Event Packet has been queued,
        # not when it's been received by the host.
        #
        # Returns False if the TX buffer is full and the MIDI Event could not be queued.
        _tx_buffer = self._tx_buffer
        w = _tx_buffer.pend_write()
        if len(w) < 4:
            return False  # TX buffer full
        w[0] = (cable << 4) | cin # first 4 bits: cable, second 4 bits: cin
        w[1] = midi0
        w[2] = midi1
        w[3] = midi2
        _tx_buffer.finish_write(4)
        self._tx_xfer()
        return True

    def _tx_xfer(self):
        # Keep an active IN transfer to send data to the host, whenever
        # there is data to send.
        _tx_buffer = self._tx_buffer
        # if self.is_open() and not self.xfer_pending(ep_in := self.ep_in) and _tx_buffer.readable():
        #     self.submit_xfer(ep_in, _tx_buffer.pend_read(), self._tx_cb)
        if self.is_open() and not self.xfer_pending(self.ep_in) and _tx_buffer.readable():
            self.submit_xfer(self.ep_in, _tx_buffer.pend_read(), self._tx_cb)

    def _tx_cb(self, ep, res, num_bytes):
        if res == 0:
            self._tx_buffer.finish_read(num_bytes)
        self._tx_xfer()

    def _rx_xfer(self):
        # Keep an active OUT transfer to receive MIDI events from the host
        _rx_buffer = self._rx_buffer
        # if self.is_open() and not self.xfer_pending(ep_out := self.ep_out) and _rx_buffer.writable():
        #     self.submit_xfer(ep_out, _rx_buffer.pend_write(), self._rx_cb)
        if self.is_open() and not self.xfer_pending(self.ep_out) and _rx_buffer.writable():
            self.submit_xfer(self.ep_out, _rx_buffer.pend_write(), self._rx_cb)

    def _rx_cb(self, ep, res, num_bytes):
        if res == 0:
            self._rx_buffer.finish_write(num_bytes)
###### avoid schedule because it makes it run on the main thread?
            schedule(self._on_rx, None)
        self._rx_xfer()

    def _on_rx(self, _):
        # Receive MIDI events. Called via micropython.schedule, outside of the USB callback function.
        _rx_buffer = self._rx_buffer
        m = _rx_buffer.pend_read()
        i = 0
        while i <= len(m) - 4:
            cable = m[i] >> 4
            cin = m[i] & 0x0F
            try:
                self._in_callbacks[cable](cin, *m[i + 1:i + 4]) # type: ignore
            except:
                pass
            i += 4
        _rx_buffer.finish_read(i)

    def on_open(self):
        super().on_open()
        # kick off any transfers that may have queued while the device was not open
        self._tx_xfer()
        self._rx_xfer()

    def desc_cfg(self, desc, itf_num, ep_num, strs):
        # Interface Association Descriptor
        desc.interface_assoc(itf_num, 2, 0x01, 0x01, 0x00)
        # AudioControl interface
        desc.interface(itf_num, 0, _INTERFACE_CLASS_AUDIO, _INTERFACE_SUBCLASS_AUDIO_CONTROL)
        desc.pack('<BBBHHBB', 9, 0x24, 0x01, 0x0100, 0x0009, 0x01, itf_num + 1)
        # MIDIStreaming interface
        ms_if_num = itf_num + 1
        desc.interface(ms_if_num, 2, _INTERFACE_CLASS_AUDIO, _INTERFACE_SUBCLASS_AUDIO_MIDISTREAMING)
        # Class-specific MIDIStreaming Interface header
        total_class_specific_len = 7 + (num_in := self.num_in) * _JACK_IN_DESC_LEN + (num_out := self.num_out) * _JACK_OUT_DESC_LEN + num_out * _JACK_IN_DESC_LEN + num_in * _JACK_OUT_DESC_LEN
        desc.pack('<BBBHH', 7, 0x24, 0x01, 0x0100, total_class_specific_len)
        # IN Jacks for each virtual IN cable
        for i in range(num_in):
            desc.pack('<BBBBBB', _JACK_IN_DESC_LEN, 0x24, 0x02, _JACK_TYPE_EMBEDDED, 1 + i, 0x00)
        # OUT Jacks for each virtual OUT cable
        for i in range(num_out):
            desc.pack('<BBBBBBBBB', _JACK_OUT_DESC_LEN, 0x24, 0x03, _JACK_TYPE_EMBEDDED, 1 + num_in + i, 0x01, 1 + i, 1, 0x00)
        # External OUT jacks for each virtual IN cable
        for i in range(num_out):
            desc.pack('<BBBBBBBBB', _JACK_OUT_DESC_LEN, 0x24, 0x03, _JACK_TYPE_EXTERNAL, 1 + num_in + num_out + i, 0x01, 1 + i, 1, 0x00)
        # External IN jacks for each virtual OUT cable
        for i in range(num_in):
            desc.pack('<BBBBBB', _JACK_IN_DESC_LEN, 0x24, 0x02, _JACK_TYPE_EXTERNAL, 1 + num_in + 2 * num_out + i, 0x00)
        # Single shared OUT endpoint
        # self.ep_out = ep_num
        # desc.pack('<BBBBHBBB', _STD_DESC_AUDIO_ENDPOINT_LEN, 0x05, ep_num, 3, 64, 0, 0, 0)
        # desc.pack('<BBBBB', _CLASS_DESC_ENDPOINT_LEN, 0x25, 0x01, num_in, *[1 + i for i in range(num_in)])
        # # Single shared IN endpoint
        # self.ep_in = (ep_in := ep_num | _EP_IN_FLAG)
        # desc.pack('<BBBBHBBB', _STD_DESC_AUDIO_ENDPOINT_LEN, 0x05, ep_in, 3, 64, 0, 0, 0)
        # desc.pack('<BBBBB', _CLASS_DESC_ENDPOINT_LEN, 0x25, 0x01, num_out, *[1 + num_in + i for i in range(num_out)])
        self.ep_out = ep_num
        desc.pack('<BBBBHBBB', _STD_DESC_AUDIO_ENDPOINT_LEN, 0x05, self.ep_out, 3, 64, 0, 0, 0)
        desc.pack('<BBBBB', _CLASS_DESC_ENDPOINT_LEN, 0x25, 0x01, num_in, *[1 + i for i in range(num_in)])
        # Single shared IN endpoint
        self.ep_in = ep_num | _EP_IN_FLAG
        desc.pack('<BBBBHBBB', _STD_DESC_AUDIO_ENDPOINT_LEN, 0x05, self.ep_in, 3, 64, 0, 0, 0)
        desc.pack('<BBBBB', _CLASS_DESC_ENDPOINT_LEN, 0x25, 0x01, num_out, *[1 + num_in + i for i in range(num_out)])

        # if desc.b:
        #     print("Config descriptor header:", list(desc.b[:9]))
        #     print("Descriptor length:", desc.o)
        #     print("Descriptor hex:", desc.b[:desc.o].hex())
        #     print("Descriptor bytes:", list(desc.b[:desc.o]))
        #     time.sleep_ms(1000)

    def num_itfs(self):
        return 2

    def num_eps(self):
        return 2