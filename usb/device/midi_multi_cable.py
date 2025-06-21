''' Multi-port USB MIDI 1.0 library for MicroPython based on multiple virtual cables approach

    This work for 1 MIDI in port and 1 MIDI out port (i.e. 1 cable per endpoint), but doesn’t work with Windows or Linux (not ntested on
    on macOS) if the number of in ports and/or the number of out ports is set to more than one
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
_MAX_CABLES = const(16)  # USB MIDI 1.0: up to 16 cables per endpoint

class MidiMulti(Interface):
    '''USB MIDI 1.0 device class supporting up to 16 ports in the form of virtual MIDI in and out cables'''

    def __init__(self, num_in=1, num_out=1):
        if not 1 <= num_in <= _MAX_CABLES:
            raise ValueError(f'num_in ({num_in}) should be between 1 and {_MAX_CABLES}')
        if not 1 <= num_out <= _MAX_CABLES:
            raise ValueError(f'num_out ({num_out}) should be between 1 and {_MAX_CABLES}')
        super().__init__()
        self.num_in = num_in
        self.num_out = num_out
        self.ep_out = None
        self.ep_in = None
        self._rx_buffer = Buffer(_EP_MIDI_PACKET_SIZE)
        self._tx_buffer = Buffer(_EP_MIDI_PACKET_SIZE)
        self._in_callbacks = [None] * num_in

    def set_in_callback(self, cable_number, callback):
        '''Register a callback for received MIDI messages on a virtual cable (port)'''
        if 0 <= cable_number < self.num_in:
            self._in_callbacks[cable_number] = callback

    # Helper functions for sending common MIDI messages

    def note_on(self, cable, channel, note, vel=0x40):
        self.send_event(cable, 0x9, 0x90 | channel, note, vel)

    def note_off(self, cable, channel, note, vel=0x40):
        self.send_event(cable, 0x8, 0x80 | channel | channel, note, vel)

    def control_change(self, cable, channel, controller, value):
        self.send_event(cable, 0xB, 0xB0 | channel, controller, value)

    def send_event(self, cable, cin, data_0, data_1=0, data_2=0):
        '''Queue a MIDI Event Packet to be sent to the host; takes a cable number (port), a USB-MIDI Code Index Number (CIN) and up to three MIDI data bytes; returns False if failed due to the TX buffer being full'''
        _tx_buffer = self._tx_buffer
        w = _tx_buffer.pend_write()
        if len(w) < 4:
            return False  # TX buffer full
        w[0] = (cable << 4) | cin # first 4 bits: cable, second 4 bits: cin
        w[1] = data_0
        w[2] = data_1
        w[3] = data_2
        _tx_buffer.finish_write(4)
        self._tx_xfer()
        return True

    def desc_cfg(self, desc, itf_num, ep_num, strs):
        # Interface Association Descriptor (TEST: try with and without IAD)
######
        # desc.interface_assoc(itf_num, 2, 0x01, 0x01, 0x00)
        # Audio Control interface
        desc.interface(itf_num, 0, 0x01, 0x01)
        desc.pack('<BBBHHBB', 9, 0x24, 0x01, 0x0100, 0x0009, 0x01, itf_num + 1)
        # MIDI Streaming interface
        ms_if_num = itf_num + 1
        desc.interface(ms_if_num, 2, 0x01, 0x03)
        # Class-specific MIDI Streaming interface header
        total_class_specific_len = 7 + (num_in := self.num_in) * 6 + (num_out := self.num_out) * 9 + num_out * 6 + num_in * 9
        desc.pack('<BBBHH', 7, 0x24, 0x01, 0x0100, total_class_specific_len)
        # Embedded IN jacks for each virtual IN cable
        for i in range(num_in):
            desc.pack('<BBBBBB', 6, 0x24, 0x02, 0x01, 1 + i, 0x00)
        # Embedded OUT jacks for each virtual OUT cable
        for i in range(num_out):
            desc.pack('<BBBBBBBBB', 9, 0x24, 0x03, 0x01, 1 + num_in + i, 0x01, 1 + i, 1, 0x00)
        # External OUT jacks for each virtual IN cable
######
        for i in range(num_out): #  (TEST: try with and without external jacks)
            desc.pack('<BBBBBBBBB', 9, 0x24, 0x03, 0x02, 1 + num_in + num_out + i, 0x01, 1 + i, 1, 0x00)
        # External IN jacks for each virtual OUT cable
######
        for i in range(num_in): #  (TEST: try with and without external jacks)
            desc.pack('<BBBBBB', 6, 0x24, 0x02, 0x02, 1 + num_in + 2 * num_out + i, 0x00)
        # Single shared OUT endpoint
        self.ep_out = ep_num
        desc.pack('<BBBBHB', 7, 0x05, ep_num, 3, 32, 1)
        desc.pack('<BBBBB', 5, 0x25, 0x01, num_in, *[1 + i for i in range(num_in)])
        # Single shared IN endpoint
        self.ep_in = (ep_in := ep_num | 0x80)
        desc.pack('<BBBBHB', 7, 0x05, ep_in, 3, 32, 1)
        desc.pack('<BBBBB', 5, 0x25, 0x01, num_out, *[1 + num_in + i for i in range(num_out)])

    def _tx_xfer(self):
        '''Keep an active IN transfer to send data to the host, whenever there is data to send'''
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
        '''Keep an active OUT transfer to receive MIDI events from the host'''
        _rx_buffer = self._rx_buffer
######
        # if self.is_open() and not self.xfer_pending(ep_out := self.ep_out) and _rx_buffer.writable():
        #     self.submit_xfer(ep_out, _rx_buffer.pend_write(), self._rx_cb)
        if self.is_open() and not self.xfer_pending(self.ep_out) and _rx_buffer.writable():
            self.submit_xfer(self.ep_out, _rx_buffer.pend_write(), self._rx_cb)

    def _rx_cb(self, ep, res, num_bytes):
        '''USB callback function to receive MIDI data'''
        if res == 0:
            self._rx_buffer.finish_write(num_bytes)
            schedule(self._on_rx, None) # (QUESTION: avoid schedule because it makes it run on the main thread?)
        self._rx_xfer()

    def _on_rx(self, _):
        '''Receive MIDI events; called from self._rx_cb via micropython.schedule'''
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

    def num_itfs(self):
        return 2

    def num_eps(self):
        return 2

    def on_open(self):
        super().on_open()
        # Kick off any transfers that may have queued while the device was not open
        self._tx_xfer()
        self._rx_xfer()