''' Multi-port USB MIDI 1.0 library for MicroPython based on a multiple streaming interface approach

    Multiple MIDI ports set up this way are recognized by Windows and Linux and shoud work on macOS as well (not tested)
    Port names are ignored by Windows and Linux, but should show on macOS (not tested)

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

_EP_MIDI_PACKET_SIZE = 64

class MidiMulti(Interface):
    '''Composite USB MIDI 1.0 device class supporting multiple ports in the form of multiple MIDI Streaming interfaces'''

    def __init__(self, num_ports=1, port_names=None):
        super().__init__()
        self.num_ports = num_ports
        self.port_names = port_names or [None for _ in range(num_ports)]
        # self.ac = MidiACInterface(self)
        self.ports = [MidiPortInterface(i, self.port_names[i]) for i in range(num_ports)]

    def set_in_callback(self, port, cb):
        '''Register a callback for received MIDI messages on a virtual cable (port)'''
        self.ports[port].set_in_callback(cb)

    # Helper functions for sending common MIDI messages

    def note_on(self, port, channel, pitch, vel=0x40):
        self.ports[port].send_event(0x9, 0x90 | channel, pitch, vel)

    def note_off(self, port, channel, pitch, vel=0x40):
        self.send_event(0x8, 0x80 | channel, pitch, vel)

    def control_change(self, port, channel, controller, value):
        self.ports[port].send_event(0xB, 0xB0 | channel, controller, value)

    def send_event(self, port, cin, data_0, data_1=0, data_2=0):
        '''Queue a MIDI Event Packet to be sent to the host; takes a port number, a USB-MIDI Code Index Number (CIN) and up to three MIDI data
        bytes; returns False if failed due to the TX buffer being full'''
        return self.ports[port].send_event(cin, data_0, data_1, data_2)

    def desc_cfg(self, desc, itf_num, ep_num, strs):
        # Audio Control interface (TEST: try with and without IAD)
######
        # self.ac.desc_cfg(desc, itf_num, ep_num, strs)
        desc.interface(itf_num, 0, 1, 1)
        # Class-specific Audio Control header, points to all MIDI Streaming interfaces following
        bLength = 8 + (num_ports := self.num_ports)
        ms_interface_numbers = list(range(itf_num + 1, itf_num + 1 + num_ports))
        desc.pack('<BBBHHB' + 'B' * num_ports, bLength, 0x24, 1, 0x0100, bLength, num_ports, *ms_interface_numbers)
        next_itf = itf_num + 1
        next_ep = ep_num
        for port in self.ports:
            port.desc_cfg(desc, next_itf, next_ep, strs)
            next_itf += port.num_itfs()
            next_ep += port.num_eps()

    def num_itfs(self):
        return 1 + self.num_ports

    def num_eps(self):
        return self.num_ports

    def on_open(self):
        super().on_open()
        # Kick off any transfers that may have queued while the device was not open
        for p in self.ports:
            p.on_open()

######
# class MidiACInterface(Interface):

#     def __init__(self, parent):
#         self.parent = parent

#     def desc_cfg(self, desc, itf_num, ep_num, strs):
#         desc.interface(itf_num, 0, 1, 1)
#         # Class-specific AC header, points to all MIDIStreaming interfaces following
#         n_ports = self.parent.num_ports
#         bLength = 8 + n_ports
#         ms_interface_numbers = list(range(itf_num + 1, itf_num + 1 + n_ports))
#         desc.pack('<BBBHHB' + 'B'*n_ports,
#                   bLength, 0x24, 1, 0x0100, bLength, n_ports, *ms_interface_numbers)        # No terminals

#     def num_itfs(self):
#         return 1
#     def num_eps(self):
#         return 0

class MidiPortInterface(Interface):
    '''Class providing one MIDIStreaming interface for one port'''

    def __init__(self, port_index, port_name=None):
        super().__init__()
        self.port_index = port_index
        self.port_name = port_name
        self.ep_out = None
        self.ep_in = None
        self._rx_buffer = Buffer(_EP_MIDI_PACKET_SIZE)
        self._tx_buffer = Buffer(_EP_MIDI_PACKET_SIZE)
        self._in_callback = None

    def set_in_callback(self, cb):
        '''Register a callback for received MIDI messages on this port (Midi Streaming interface)'''
        self._in_callback = cb

    def send_event(self, cin, data_0, data_1=0, data_2=0):
        '''Queue a MIDI Event Packet to be sent to the host on this port; takes a USB-MIDI Code Index Number (CIN) and up to three MIDI data
        bytes; returns False if failed due to the TX buffer being full'''
        _tx_buffer = self._tx_buffer
        w = _tx_buffer.pend_write()
        if len(w) < 4:
            return False
######
        w[0] = (0 << 4) | cin
        w[1] = data_0
        w[2] = data_1
        w[3] = data_2
        _tx_buffer.finish_write(4)
        self._tx_xfer()
        return True

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
        port = self.port_index
        _rx_buffer = self._rx_buffer
        m = _rx_buffer.pend_read()
        i = 0
        while i <= len(m) - 4:
            cin = m[i] & 0x0F
            try:
                self._in_callback(port, cin, *m[i + 1:i + 4]) # type: ignore
            except:
                pass
            i += 4
        _rx_buffer.finish_read(i)

    def desc_cfg(self, desc, itf_num, ep_num, strs):
        if self.port_name is not None:
            iInterface = len(strs)
            strs.append(self.port_name)
        else:
            iInterface = 0
        jack_in_id = 1 + 2 * self.port_index
        Jack_out_id = jack_in_id + 1
        # MIDI Streaming interface
        desc.interface(itf_num, 2, 1, 3, 0, iInterface)
        # Class-specific MIDI Streaming header
        desc.pack('<BBBHH', 7, 0x24, 1, 0x0100, 25)
        # Embedded in Jack
        desc.pack('<BBBBBB', 6, 0x24, 2, 1, jack_in_id, 0)
        # Embedded out Jack
        desc.pack('<BBBBBBBBB', 9, 0x24, 3, 1, Jack_out_id, 1, jack_in_id, 1, 0)
        # Out endpoint
        self.ep_out = ep_num
        desc.pack('<BBBBHB', 7, 5, self.ep_out, 3, 32, 1)
        desc.pack('<BBBBB', 5, 0x25, 1, 1, jack_in_id)
        # In endpoint
        self.ep_in = ep_num | 0x80
        desc.pack('<BBBBHB', 7, 5, self.ep_in, 3, 32, 1)
        desc.pack('<BBBBB', 5, 0x25, 1, 1, Jack_out_id)

    def num_itfs(self):
        return 1

    def num_eps(self):
        return 1

    def on_open(self):
        super().on_open()
        # Kick off any transfers that may have queued while the device was not open
        self._tx_xfer()
        self._rx_xfer()