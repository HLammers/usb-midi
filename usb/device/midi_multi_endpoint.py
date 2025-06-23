''' Multi-port USB MIDI 1.0 library for MicroPython based on a multiple Endpoints approach

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
_JACK_TYPE = const(1) # 1 = Embedded, 2 = External

class MidiMulti(Interface):
    '''USB MIDI 1.0 device class supporting multiple MIDI ports in the form of multiple endpoints '''

    def __init__(self, num_in=1, num_out=1, in_names=None, out_names=None):
        super().__init__()
        self.num_in = num_in
        self.num_out = num_out
        self.in_names = in_names or [None for _ in range(num_in)]
        self.out_names = out_names or [None for _ in range(num_out)]
        self.ep_out = [None] * num_in
        self.ep_in  = [None] * num_out
        self._rx_buffers = [Buffer(_EP_MIDI_PACKET_SIZE) for _ in range(num_in)]
        self._tx_buffers = [Buffer(_EP_MIDI_PACKET_SIZE) for _ in range(num_out)]
        self._in_callbacks = [None] * num_in

    def set_in_callback(self, port, cb):
        '''Register a callback for received MIDI messages on a port (Endpoint)'''
        self._in_callbacks[port] = cb

    # Helper functions for sending common MIDI messages

    def note_on(self, port, channel, note, vel=0x40):
        self.send_event(port, 0x9, 0x90 | channel, note, vel)

    def note_off(self, port, channel, note, vel=0x40):
        self.send_event(port, 0x8, 0x80 | channel, note, vel)

    def control_change(self, port, channel, controller, value):
        self.send_event(port, 0xB, 0xB0 | channel, controller, value)

    def send_event(self, port, cin, data_0, data_1=0, data_2=0):
        '''Queue a MIDI Event Packet to be sent to the host; takes a port number, a USB-MIDI Code Index Number (CIN) and up to three MIDI data
        bytes; returns False if failed due to the TX buffer being full'''
        _buffer = self._tx_buffers[port]
        w = _buffer.pend_write()
        if len(w) < 4:
            return False # TX buffer full
        w[0] = cin
        w[1] = data_0
        w[2] = data_1
        w[3] = data_2
        _buffer.finish_write(4)
        self._tx_xfer(port)
        return True

    def desc_cfg(self, desc, itf_num, ep_num, strs):
        # Interface Association Descriptor

        # If usb.device is initiated without builtin_driver=True, but with device_class=0xEF, device_subclass=2, device_protocol=1 (which is
        # needed with builtin_driver=True, because that adds an IAD), USBView on windows shows an error that device_class, device_subclass and
        # device_protocol should only be inlcuded if an IAD is included, but it works anyway. desc.interface_assoc(itf_num, 2, 1, 1, 0) adds
        # an IAD, which solves the above mentioned error, but then the MIDI ports are not reconginised correctly anymore. 

        # desc.interface_assoc(itf_num, 2, 1, 1, 0)
        # Audio Control interface
        desc.interface(itf_num, 0, 1, 1)
        _pack = desc.pack
        _pack('<BBBHHBB', 9, 0x24, 1, 0x0100, 9, 1, itf_num + 1)
        # MIDI Streaming interface
        ms_if_num = itf_num + 1
        desc.interface(ms_if_num, (num_in := self.num_in) + (num_out := self.num_out), 1, 3)
        # Class-specific MIDI Streaming interface header
        cs_len = 7 + num_in * 6 + num_out * 9
        _pack('<BBBHH', 7, 0x24, 1, 0x0100, cs_len)
        # In Jacks
        in_names = self.in_names
        for i in range(num_in):
            if (name := in_names[i]) is None:
                iJack = 0
            else:
                iJack = len(strs)
                strs.append(name)
            _pack('<BBBBBB', 6, 0x24, 2, _JACK_TYPE, 1 + i, iJack)
        # Out Jacks
        out_names = self.out_names
        for i in range(num_out):
            if (name := out_names[i]) is None:
                iJack = 0
            else:
                iJack = len(strs)
                strs.append(name)
            _pack('<BBBBBBBBB', 9, 0x24, 3, _JACK_TYPE, 1 + num_in + i, 1, 1 + i, 1, iJack)
        endpoint_id = ep_num
        # Out Endpoints
        for i in range(num_in):
            self.ep_out[i] = endpoint_id
            # _pack('<BBBBHB', 7, 5, endpoint_id, 3, 32, 1) # interupt
            _pack('<BBBBHB', 7, 5, endpoint_id, 2, 32, 0) # bulk
            _pack('<BBBBB', 5, 0x25, 1, 1, 1 + i)
            endpoint_id += 1
        # In Endpoints
        for i in range(num_out):
            self.ep_in[i] = (ep_id := endpoint_id | 0x80)
            # _pack('<BBBBHB', 7, 5, ep_id, 3, 32, 1) # interupt
            _pack('<BBBBHB', 7, 5, ep_id, 2, 32, 0) # bulk
            _pack('<BBBBB', 5, 0x25, 1, 1, 1 + self.num_in + i)
            endpoint_id += 1

    def num_itfs(self):
        return 2

    def num_eps(self):
        return self.num_in + self.num_out

    def on_open(self):
        super().on_open()
        _xfer = self._tx_xfer
        for i in range(self.num_out):
            _xfer(i)
        _xfer = self._rx_xfer
        for i in range(self.num_in):
            _xfer(i)

    def _tx_xfer(self, port):
        '''Keep an active IN transfer to send data to the host, whenever there is data to send'''
######
        log(f'_tx_xfer {port}')
        _buffer = self._tx_buffers[port]
        ep = self.ep_in[port]
        if self.is_open() and not self.xfer_pending(ep) and _buffer.readable():
            self.submit_xfer(ep, _buffer.pend_read(), lambda ep, res, n: self._tx_cb(port, ep, res, n))

    def _tx_cb(self, port, ep, res, num_bytes):
######
        log(f'_tx_cb {port}')
        if res == 0:
            self._tx_buffers[port].finish_read(num_bytes)
        self._tx_xfer(port)

    def _rx_xfer(self, port):
######
        log(f'_rx_xfer {port}')
        '''Keep an active OUT transfer to receive MIDI events from the host'''
        _buffer = self._rx_buffers[port]
        ep = self.ep_out[port]
        if self.is_open() and not self.xfer_pending(ep) and _buffer.writable():
            self.submit_xfer(ep, _buffer.pend_write(), lambda ep, res, n: self._rx_cb(port, ep, res, n))

    def _rx_cb(self, port, ep, res, num_bytes):
######
        log(f'_rx_cb {port}')
        '''USB callback function to receive MIDI data'''
        if res == 0:
            self._rx_buffers[port].finish_write(num_bytes)
            schedule(lambda _: self._on_rx(port), None) # (QUESTION: avoid schedule because it makes it run on the main thread?)
        self._rx_xfer(port)

    def _on_rx(self, port):
######
        log(f'_on_rx {port}')
        '''Receive MIDI events; called from self._rx_cb via micropython.schedule'''
        _buffer = self._rx_buffers[port]
        m = _buffer.pend_read()
        _callback = self._in_callbacks[port]
        i = 0
        while i <= len(m) - 4:
            cin = m[i] & 0x0F
            try:
                _callback(port, cin, *m[i + 1:i + 4])
            except:
                pass
            i += 4
        _buffer.finish_read(i)

######
def log(msg):
    with open('/log.txt', 'a') as f:
        f.write(msg + '\n')