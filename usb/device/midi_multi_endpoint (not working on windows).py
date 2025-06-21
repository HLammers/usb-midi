''' Multi-port USB MIDI 1.0 library for MicroPython based on a multiple endpoints approach

    Multiple MIDI ports set up this way are recognized Linux, but not on Windows (not tested on macOS)
    Port names are not (yet) implemented

    This library is still in testing phase and further development might introduce breaking changes

    Requires the micropython-lib usb-device library (https://github.com/micropython/micropython-lib/tree/master/micropython/usb) and
    replaces the micropython-lib usb-device-midi library (which only supports a single port)

    Copyright (c) 2025 Harm Lammers
    
    Parts are taken from the micropython-lib usb-device-midi library, copyright (c) 2023 Paul Hamshere, 2023-2024 Angus Gratton, published
    under MIT licence

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

from micropython import schedule
from usb.device.core import Interface, Buffer

_EP_MIDI_PACKET_SIZE = const(64)

class MidiMulti(Interface):
    """
    MIDI device: single MIDIStreaming interface, multiple IN/OUT endpoints and jacks.
    Each endpoint/jack is a separate MIDI port.
    """

    def __init__(self, num_in=1, num_out=1):
        super().__init__()
        self.num_in = num_in
        self.num_out = num_out
        self.ep_out = [None] * num_in   # RX endpoints (host->device)
        self.ep_in  = [None] * num_out  # TX endpoints (device->host)
        self._rx_buffer = [Buffer(_EP_MIDI_PACKET_SIZE) for _ in range(num_in)]
        self._tx_buffer = [Buffer(_EP_MIDI_PACKET_SIZE) for _ in range(num_out)]
        self._in_callbacks = [None] * num_in

    def set_in_callback(self, port, cb):
        if 0 <= port < self.num_in:
            self._in_callbacks[port] = cb

    def note_on(self, port, channel, pitch, vel=0x40):
        self.send_event(port, 0x9, 0x90 | channel, pitch, vel)
    def note_off(self, port, channel, pitch, vel=0x40):
        self.send_event(port, 0x8, 0x80 | channel, pitch, vel)
    def control_change(self, port, channel, controller, value):
        self.send_event(port, 0xB, 0xB0 | channel, controller, value)
    def send_event(self, port, cin, midi0, midi1=0, midi2=0):
        # TX endpoint/port index
        _tx_buffer = self._tx_buffer[port]
        w = _tx_buffer.pend_write()
        if len(w) < 4:
            return False
        w[0] = cin  # cable=0
        w[1] = midi0
        w[2] = midi1
        w[3] = midi2
        _tx_buffer.finish_write(4)
        self._tx_xfer(port)
        return True

    def _tx_xfer(self, port):
        buf = self._tx_buffer[port]
        ep = self.ep_in[port]
        if self.is_open() and not self.xfer_pending(ep) and buf.readable():
            self.submit_xfer(ep, buf.pend_read(), lambda ep, res, n: self._tx_cb(port, ep, res, n))
    def _tx_cb(self, port, ep, res, num_bytes):
        buf = self._tx_buffer[port]
        if res == 0:
            buf.finish_read(num_bytes)
        self._tx_xfer(port)
    def _rx_xfer(self, port):
        buf = self._rx_buffer[port]
        ep = self.ep_out[port]
        if self.is_open() and not self.xfer_pending(ep) and buf.writable():
            self.submit_xfer(ep, buf.pend_write(), lambda ep, res, n: self._rx_cb(port, ep, res, n))
    def _rx_cb(self, port, ep, res, num_bytes):
        buf = self._rx_buffer[port]
        if res == 0:
            buf.finish_write(num_bytes)
            schedule(lambda _: self._on_rx(port), None)
        self._rx_xfer(port)
    def _on_rx(self, port):
        buf = self._rx_buffer[port]
        m = buf.pend_read()
        i = 0
        while i <= len(m) - 4:
            cable = m[i] >> 4
            cin = m[i] & 0x0F
            try:
                cb = self._in_callbacks[port]
                if cb:
                    cb(cable, cin, *m[i + 1:i + 4])
            except:
                pass
            i += 4
        buf.finish_read(i)

    def on_open(self):
        super().on_open()
        for i in range(self.num_out):
            self._tx_xfer(i)
        for i in range(self.num_in):
            self._rx_xfer(i)

    def desc_cfg(self, desc, itf_num, ep_num, strs):
        # 1. AudioControl interface
        desc.interface(itf_num, 0, 1, 1)
        # AC header, points to MIDIStreaming interface
        desc.pack('<BBBHHBB', 9, 0x24, 1, 0x0100, 9, 1, itf_num + 1)

        # 2. MIDIStreaming interface
        ms_if_num = itf_num + 1
        desc.interface(ms_if_num, self.num_in + self.num_out, 1, 3)
        # -- Class-specific MS header: total length calculation
        cs_len = 7 + (self.num_in * 6) + (self.num_out * 9)
        desc.pack('<BBBHH', 7, 0x24, 1, 0x0100, cs_len)

        # -- Embedded IN jacks (for OUT endpoints)
        for i in range(self.num_in):
            desc.pack('<BBBBBB', 6, 0x24, 2, 1, 1 + i, 0)
        # -- Embedded OUT jacks (for IN endpoints)
        for i in range(self.num_out):
            desc.pack('<BBBBBBBBB', 9, 0x24, 3, 1, 1 + self.num_in + i, 1, 1 + i, 1, 0)

        ep_addr = ep_num
        # -- OUT endpoints (host->device)
        for i in range(self.num_in):
            self.ep_out[i] = ep_addr
            desc.pack('<BBBBHB', 7, 5, ep_addr, 3, 32, 1)
            # Class-specific endpoint for OUT
            desc.pack('<BBBBB', 5, 0x25, 1, 1, 1 + i)  # jack=1+i
            ep_addr += 1
        # -- IN endpoints (device->host)
        for i in range(self.num_out):
            self.ep_in[i] = ep_addr | 0x80
            desc.pack('<BBBBHB', 7, 5, self.ep_in[i], 3, 32, 1)
            # Class-specific endpoint for IN
            desc.pack('<BBBBB', 5, 0x25, 1, 1, 1 + self.num_in + i)  # jack=1+num_in+i
            ep_addr += 1

    def num_itfs(self):
        return 2
    def num_eps(self):
        return self.num_in + self.num_out