from micropython import schedule
from usb.device.core import Interface, Buffer
import time

_INTERFACE_CLASS_AUDIO = const(0x01)
_INTERFACE_SUBCLASS_AUDIO_CONTROL = const(0x01)
_INTERFACE_SUBCLASS_AUDIO_MIDISTREAMING = const(0x03)
_JACK_TYPE_EMBEDDED = const(0x01)
_EP_IN_FLAG = const(0x80)
_EP_MIDI_PACKET_SIZE = 64

class MidiACInterface(Interface):

    def __init__(self, parent):
        self.parent = parent

    def desc_cfg(self, desc, itf_num, ep_num, strs):
        desc.interface(itf_num, 0, _INTERFACE_CLASS_AUDIO, _INTERFACE_SUBCLASS_AUDIO_CONTROL)
        # Class-specific AC header, points to all MIDIStreaming interfaces following
        n_ports = self.parent.num_ports
        bLength = 8 + n_ports
        ms_interface_numbers = list(range(itf_num + 1, itf_num + 1 + n_ports))
        desc.pack('<BBBHHB' + 'B'*n_ports,
                  bLength, 0x24, 0x01, 0x0100, bLength, n_ports, *ms_interface_numbers)        # No terminals

    def num_itfs(self):
        return 1
    def num_eps(self):
        return 0

class MidiPortInterface(Interface):
    # One MIDIStreaming interface for one port
    def __init__(self, port_index, port_name=None):
        super().__init__()
        self.port_index = port_index
        self.port_name = port_name or f"MIDI Port {port_index+1}"
        self.ep_out = None
        self.ep_in = None
        self._rx_buffer = Buffer(_EP_MIDI_PACKET_SIZE)
        self._tx_buffer = Buffer(_EP_MIDI_PACKET_SIZE)
        self._in_callback = None

    def set_in_callback(self, cb):
        self._in_callback = cb

    def note_on(self, channel, pitch, vel=0x40):
        self.send_event(0, 0x9, 0x90 | channel, pitch, vel)
    def note_off(self, channel, pitch, vel=0x40):
        self.send_event(0, 0x8, 0x80 | channel, pitch, vel)
    def control_change(self, channel, controller, value):
        self.send_event(0, 0xB, 0xB0 | channel, controller, value)

    def send_event(self, cable, cin, midi0, midi1=0, midi2=0):
        _tx_buffer = self._tx_buffer
        w = _tx_buffer.pend_write()
        if len(w) < 4:
            return False
        w[0] = (cable << 4) | cin
        w[1] = midi0
        w[2] = midi1
        w[3] = midi2
        _tx_buffer.finish_write(4)
        self._tx_xfer()
        return True

    def _tx_xfer(self):
        _tx_buffer = self._tx_buffer
        if self.is_open() and not self.xfer_pending(self.ep_in) and _tx_buffer.readable():
            self.submit_xfer(self.ep_in, _tx_buffer.pend_read(), self._tx_cb)
    def _tx_cb(self, ep, res, num_bytes):
        if res == 0:
            self._tx_buffer.finish_read(num_bytes)
        self._tx_xfer()
    def _rx_xfer(self):
        _rx_buffer = self._rx_buffer
        if self.is_open() and not self.xfer_pending(self.ep_out) and _rx_buffer.writable():
            self.submit_xfer(self.ep_out, _rx_buffer.pend_write(), self._rx_cb)
    def _rx_cb(self, ep, res, num_bytes):
        if res == 0:
            self._rx_buffer.finish_write(num_bytes)
            schedule(self._on_rx, None)
        self._rx_xfer()
    def _on_rx(self, _):
        _rx_buffer = self._rx_buffer
        m = _rx_buffer.pend_read()
        i = 0
        while i <= len(m) - 4:
            cable = m[i] >> 4
            cin = m[i] & 0x0F
            try:
                if self._in_callback:
                    self._in_callback(cable, cin, *m[i + 1:i + 4])
            except:
                pass
            i += 4
        _rx_buffer.finish_read(i)

    def on_open(self):
        super().on_open()
        self._tx_xfer()
        self._rx_xfer()

    def desc_cfg(self, desc, itf_num, ep_num, strs):
        # Interface Association Descriptor
        # MIDIStreaming interface
        desc.interface(itf_num, 2, _INTERFACE_CLASS_AUDIO, _INTERFACE_SUBCLASS_AUDIO_MIDISTREAMING)
        # Class-specific MS header (points to just this interface)
        desc.pack('<BBBHH', 7, 0x24, 0x01, 0x0100, 25)
        # Embedded IN Jack
        desc.pack('<BBBBBB', 6, 0x24, 0x02, _JACK_TYPE_EMBEDDED, 1, 0)
        # Embedded OUT Jack
        desc.pack('<BBBBBBBBB', 9, 0x24, 0x03, _JACK_TYPE_EMBEDDED, 2, 1, 1, 1, 0)
        # OUT endpoint (host->device)
        self.ep_out = ep_num
        desc.pack('<BBBBHB', 7, 0x05, self.ep_out, 3, 32, 1)
        desc.pack('<BBBBB', 5, 0x25, 0x01, 1, 1)
        # IN endpoint (device->host)
        self.ep_in = ep_num | _EP_IN_FLAG
        desc.pack('<BBBBHB', 7, 0x05, self.ep_in, 3, 32, 1)
        desc.pack('<BBBBB', 5, 0x25, 0x01, 1, 2)

    def num_itfs(self):
        return 1
    def num_eps(self):
        return 1

class MidiMulti(Interface):
    '''
    Composite MIDI device with multiple MIDIStreaming interfaces (multi-port)
    '''
    def __init__(self, num_ports=1, port_names=None):
        super().__init__()
        self.num_ports = num_ports
        self.port_names = port_names or [f"MIDI Port {i+1}" for i in range(num_ports)]
        self.ac = MidiACInterface(self)
        self.ports = [MidiPortInterface(i, self.port_names[i]) for i in range(num_ports)]

    def set_in_callback(self, port, cb):
        self.ports[port].set_in_callback(cb)

    def note_on(self, port, channel, pitch, vel=0x40):
        self.ports[port].note_on(channel, pitch, vel)
    def note_off(self, port, channel, pitch, vel=0x40):
        self.ports[port].note_off(channel, pitch, vel)
    def control_change(self, port, channel, controller, value):
        self.ports[port].control_change(channel, controller, value)
    def send_event(self, port, cable, cin, midi0, midi1=0, midi2=0):
        self.ports[port].send_event(cable, cin, midi0, midi1, midi2)

    def desc_cfg(self, desc, itf_num, ep_num, strs):
        # AudioControl interface first
        self.ac.desc_cfg(desc, itf_num, ep_num, strs)
        next_itf = itf_num + 1
        next_ep = ep_num
        for p in self.ports:
            p.desc_cfg(desc, next_itf, next_ep, strs)
            next_itf += p.num_itfs()
            next_ep += p.num_eps()

    def num_itfs(self):
        return 1 + self.num_ports
    def num_eps(self):
        return self.num_ports

    def on_open(self):
        super().on_open()
        for p in self.ports:
            p.on_open()