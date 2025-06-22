''' Multi-port USB MIDI 1.0 library for MicroPython based on multiple virtual cables approach

    This work for 1 MIDI in port and 1 MIDI out port (i.e. 1 cable per Endpoint), but doesn’t work with Windows or Linux (not ntested on
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
_JACK_TYPE = const(1) # 1 = Embedded, 2 = External
_MAX_CABLES = const(16)  # USB MIDI 1.0: up to 16 cables per Endpoint

class MidiMulti(Interface):
    '''USB MIDI 1.0 device class supporting up to 16 MIDI ports in the form of virtual MIDI in and out cables'''

    def __init__(self, num_in=1, num_out=1, in_names=None, out_names=None):
        if not 1 <= num_in <= _MAX_CABLES:
            raise ValueError(f'num_in ({num_in}) should be between 1 and {_MAX_CABLES}')
        if not 1 <= num_out <= _MAX_CABLES:
            raise ValueError(f'num_out ({num_out}) should be between 1 and {_MAX_CABLES}')
        super().__init__()
        self.num_in = num_in
        self.num_out = num_out
        self.in_names = in_names or [None for _ in range(num_in)]
        self.out_names = out_names or [None for _ in range(num_out)]
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
        '''Queue a MIDI Event Packet to be sent to the host; takes a cable number (port), a USB-MIDI Code Index Number (CIN) and up to three
        MIDI data bytes; returns False if failed due to the TX buffer being full'''
        _buffer = self._tx_buffer
        w = _buffer.pend_write()
        if len(w) < 4:
            return False # TX buffer full
        w[0] = (cable << 4) | cin # First 4 bits: cable, second 4 bits: cin
        w[1] = data_0
        w[2] = data_1
        w[3] = data_2
        _buffer.finish_write(4)
        self._tx_xfer()
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
        _pack('<BBBHHBB', 9, 0x24, 1, 0x0100, 0x0009, 1, itf_num + 1)
        # MIDI Streaming interface
        ms_if_num = itf_num + 1
        desc.interface(ms_if_num, 2, 1, 3, 0, 0)
        # Class-specific MIDI Streaming interface header
        total_class_specific_len = 7 + (num_in := self.num_in) * 6 + (num_out := self.num_out) * 9 + num_out * 6 + num_in * 9
        _pack('<BBBHH', 7, 0x24, 1, 0x0100, total_class_specific_len)
######
        # # In Jacks for each virtual in cable
        # in_names = self.in_names
        # for i in range(num_in):
        #     if (name := in_names[i]) is None:
        #         iJack = 0
        #     else:
        #         iJack = len(strs)
        #         strs.append(name)
        #     _pack('<BBBBBB', 6, 0x24, 2, _JACK_TYPE, 1 + i, iJack)
        # # Out Jacks for each virtual out cable
        # out_names = self.out_names
        # for i in range(num_out):
        #     if (name := out_names[i]) is None:
        #         iJack = 0
        #     else:
        #         iJack = len(strs)
        #         strs.append(name)
        #     _pack('<BBBBBBBBB', 9, 0x24, 3, _JACK_TYPE, 1 + num_in + i, 1, 1 + i, 1, iJack)
        # # Single shared out Endpoint
        # self.ep_out = ep_num
        # _pack('<BBBBHB', 7, 5, ep_num, 3, 32, 1)
        # _pack('<BBBBB', 5, 0x25, 1, num_in, *[1 + i for i in range(num_in)])
        # # Single shared in Endpoint
        # self.ep_in = (ep_in := ep_num | 0x80)
        # _pack('<BBBBHB', 7, 5, ep_in, 3, 32, 1)
        # _pack('<BBBBB', 5, 0x25, 1, num_out, *[1 + num_in + i for i in range(num_out)])
######
        # In jacks for each virtual in cable and out jacks for each virtual out cable
        jack_in_ids = []
        jack_out_ids = []
        in_names = self.in_names
        out_names = self.out_names
        jack_id = 1
        for i in range(max(num_in, num_out)):
            # In Jacks for each virtual in cable
            if i < num_in:
                if (name := in_names[i]) is None:
                    iJack = 0
                else:
                    iJack = len(strs)
                    strs.append(name)
                _pack('<BBBBBB', 6, 0x24, 2, _JACK_TYPE, (jack_in_id := jack_id), iJack)
                jack_in_ids.append(jack_id)
                jack_id += 1
            # Out Jacks for each virtual out cable
            if i < num_out:
                if (name := out_names[i]) is None:
                    iJack = 0
                else:
                    iJack = len(strs)
                    strs.append(name)
                _pack('<BBBBBBBBB', 9, 0x24, 3, _JACK_TYPE, jack_id, 1, jack_in_id, 1, iJack)
                jack_out_ids.append(jack_id)
                jack_id += 1
        # Single shared out Endpoint
        self.ep_out = ep_num
        _pack('<BBBBHB', 7, 5, ep_num, 3, 32, 1)
######
        # _pack('<BBBBB', 5, 0x25, 1, num_in, *jack_in_ids)
        _pack('<BBBB' + num_in * 'B', 4 + num_in, 0x25, 1, num_in, *jack_in_ids)
        # Single shared in Endpoint
        self.ep_in = (ep_in := ep_num | 0x80)
        _pack('<BBBBHB', 7, 5, ep_in, 3, 32, 1)
######
        # _pack('<BBBBB', 5, 0x25, 1, num_out, *jack_out_ids)
        _pack('<BBBB' + num_out * 'B', 4 + num_out, 0x25, 1, num_out, *jack_out_ids)

    def _tx_xfer(self):
        '''Keep an active IN transfer to send data to the host, whenever there is data to send'''
        _buffer = self._tx_buffer
        if self.is_open() and not self.xfer_pending(ep_in := self.ep_in) and _buffer.readable():
            self.submit_xfer(ep_in, _buffer.pend_read(), self._tx_cb)

    def _tx_cb(self, ep, res, num_bytes):
        if res == 0:
            self._tx_buffer.finish_read(num_bytes)
        self._tx_xfer()

    def _rx_xfer(self):
        '''Keep an active OUT transfer to receive MIDI events from the host'''
        _buffer = self._rx_buffer
        if self.is_open() and not self.xfer_pending(ep_out := self.ep_out) and _buffer.writable():
            self.submit_xfer(ep_out, _buffer.pend_write(), self._rx_cb)

    def _rx_cb(self, ep, res, num_bytes):
        '''USB callback function to receive MIDI data'''
        if res == 0:
            self._rx_buffer.finish_write(num_bytes)
            schedule(self._on_rx, None) # (QUESTION: avoid schedule because it makes it run on the main thread?)
        self._rx_xfer()

    def _on_rx(self, _):
        '''Receive MIDI events; called from self._rx_cb via micropython.schedule'''
        _buffer = self._rx_buffer
        m = _buffer.pend_read()
        _callbacks = self._in_callbacks
        i = 0
        while i <= len(m) - 4:
            cable = m[i] >> 4
            cin = m[i] & 0x0F
            try:
                _callbacks[cable](cin, *m[i + 1:i + 4]) # type: ignore
            except:
                pass
            i += 4
        _buffer.finish_read(i)

    def num_itfs(self):
        return 2

    def num_eps(self):
        return 2

    def on_open(self):
        super().on_open()
        # Kick off any transfers that may have queued while the device was not open
        self._tx_xfer()
        self._rx_xfer()