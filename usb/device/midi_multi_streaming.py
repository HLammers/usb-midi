''' Multi-port USB MIDI 1.0 library for MicroPython based on a multiple MIDI Streaming interface approach

    Multiple MIDI ports set up this way are recognized by Windows and Linux and shoud work on macOS as well (not tested)
    Port names are ignored by Windows, but shown by Linux and might be shown by macOS (not tested)
    If usb.device is initiatied with builtin_driver=True this approach doesn’t work with windows (with or without names assigned)
    If port names are defined, these need to be longer than one character, otherwise Windows draws a GeneralFailure error (this might be either
    a Windows quirk or a bug in machine.USBDevice)
    
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
    '''Composite USB MIDI 1.0 device class supporting multiple MIDI ports in the form of multiple MIDI Streaming interfaces'''

    def __init__(self, num_ports=1, in_names=None, out_names=None):
        super().__init__()
        self.num_ports = num_ports
        self.in_names = in_names or [None for _ in range(num_ports)]
        self.out_names = out_names or [None for _ in range(num_ports)]
        self.ports = [MidiPortInterface(i, self.in_names[i], self.out_names[i]) for i in range(num_ports)]

    def set_in_callback(self, port, cb):
        '''Register a callback for received MIDI messages on a port (MIDI Streaming interface)'''
        self.ports[port].set_in_callback(cb)

    # Helper functions for sending common MIDI messages

    def note_on(self, port, channel, pitch, vel=0x40):
        self.ports[port].send_event(0x9, 0x90 | channel, pitch, vel)

    def note_off(self, port, channel, pitch, vel=0x40):
        self.ports[port].send_event(0x8, 0x80 | channel, pitch, vel)

    def control_change(self, port, channel, controller, value):
        self.ports[port].send_event(0xB, 0xB0 | channel, controller, value)

    def send_event(self, port, cin, data_0, data_1=0, data_2=0):
        '''Queue a MIDI Event Packet to be sent to the host; takes a port number, a USB-MIDI Code Index Number (CIN) and up to three MIDI data
        bytes; returns False if failed due to the TX buffer being full'''
        return self.ports[port].send_event(cin, data_0, data_1, data_2)

    def desc_cfg(self, desc, itf_num, ep_num, strs):
        # Interface Association Descriptor

        # If usb.device is initiated without builtin_driver=True, but with device_class=0xEF, device_subclass=2, device_protocol=1 (which is
        # needed with builtin_driver=True, because that adds an IAD), USBView on windows shows an error that device_class, device_subclass and
        # device_protocol should only be inlcuded if an IAD is included, but it works anyway. desc.interface_assoc(itf_num, 2, 1, 1, 0) adds
        # an IAD, which solves the above mentioned error, but then the MIDI ports are not reconginised correctly anymore. 

        # desc.interface_assoc(itf_num, 2, 1, 1, 0)
        # Audio Control interface
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

class MidiPortInterface(Interface):
    '''Class providing one MIDIStreaming interface for one port'''

    def __init__(self, port_index, in_name=None, out_name=None):
        super().__init__()
        self.port_index = port_index
        self.in_name = in_name
        self.out_name = out_name
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
        _buffer = self._tx_buffer
        w = _buffer.pend_write()
        if len(w) < 4:
            return False # TX buffer full
        w[0] = cin
        w[1] = data_0
        w[2] = data_1
        w[3] = data_2
        _buffer.finish_write(4)
        self._tx_xfer()
        return True

    def desc_cfg(self, desc, itf_num, ep_num, strs):
        jack_in_id = 1 + 2 * (port := self.port_index)
        jack_out_id = jack_in_id + 1
        # MIDI Streaming interface
        # If names are assigned to Jacks, but not to MIDI Streaming interfaces, the MIDI ports will not be shown on Linux
        iInterface = len(strs)
        strs.append(f'MS{port}')
        desc.interface(itf_num, 2, 1, 3, 0, iInterface)
        # Class-specific MIDI Streaming header
        _pack = desc.pack
        _pack('<BBBHH', 7, 0x24, 1, 0x0100, 25)
        # In Jack
        if (name := self.in_name) is None:
            iJack = 0
        else:
            iJack = len(strs)
            strs.append(name)
        _pack('<BBBBBB', 6, 0x24, 2, _JACK_TYPE, jack_in_id, iJack)
        # Out Jack
        if (name := self.in_name) is None:
            iJack = 0
        else:
            iJack = len(strs)
            strs.append(name)
        _pack('<BBBBBBBBB', 9, 0x24, 3, _JACK_TYPE, jack_out_id, 1, jack_in_id, 1, iJack)
        # Out Endpoint
        self.ep_out = ep_num
        _pack('<BBBBHB', 7, 5, ep_num, 3, 32, 1) # interupt
        # _pack('<BBBBHB', 7, 5, ep_num, 2, 32, 0) # bulk
        desc.pack('<BBBBB', 5, 0x25, 1, 1, jack_in_id)
        # In Endpoint
        self.ep_in = (ep_in := ep_num | 0x80)
        _pack('<BBBBHB', 7, 5, ep_in, 3, 32, 1) # interupt
        # _pack('<BBBBHB', 7, 5, ep_in, 2, 32, 0) # bulk
        _pack('<BBBBB', 5, 0x25, 1, 1, jack_out_id)

    def num_itfs(self):
        return 1

    def num_eps(self):
        return 1

    def on_open(self):
        super().on_open()
        # Kick off any transfers that may have queued while the device was not open
        self._tx_xfer()
        self._rx_xfer()

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
        port = self.port_index
        _buffer = self._rx_buffer
        m = _buffer.pend_read()
        _callback = self._in_callback
        i = 0
        while i <= len(m) - 4:
            cin = m[i] & 0x0F
            try:
                _callback(port, cin, *m[i + 1:i + 4]) # type: ignore
            except:
                pass
            i += 4
        _buffer.finish_read(i)