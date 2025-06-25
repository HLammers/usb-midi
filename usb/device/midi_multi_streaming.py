''' Multi-port USB MIDI 1.0 library for MicroPython based on a multiple MIDI Streaming interface approach

    - Multiple MIDI ports set up this way are recognized by Windows (if builtin_driver=False) and Linux and shoud work on macOS as well (not
      tested)
    - An asymmetric approach (unequal number of IN and OUT ports) works with Linux, but not with Windows (not tested with macOS)
    - Port names are ignored by Windows, but shown by Linux and might be shown by macOS (not tested)
    - If different names are assigned to IN and OUT Jacks, only the ones for the IN Jacks are for IN and OUT, by Linux and apparently by
      macOS too (not tested) - the strange thing is that the minimum requirement for the names to show up is that they are assigned to the
      Embedded OUT Jacks (Embedded IN Jacks are assigned the same name to make it work in asymmetric cases)
    - If usb.device is initiatied with builtin_driver=True this approach doesn’t work with windows (with or without names assigned)
    - If port names are defined, these need to be longer than one character, otherwise Windows draws a GeneralFailure error (this might be
      either a Windows quirk or a bug in machine.USBDevice)
    
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

_BUFFER_SIZE        = const(16)
_EP_PACKET_SIZE     = const(64)
_ADD_EXTERNAL_JACKS = const(False) # External Jacks are optional

class MidiMulti(Interface):
    '''Composite USB MIDI 1.0 device class supporting multiple MIDI ports in the form of multiple MIDI Streaming interfaces'''

    def __init__(self, num_in=1, num_out=1, port_names=None):
        super().__init__()
        self.num_in = num_in
        self.num_out = num_out
        self.num_str_itfs = (num_str_itfs := max(num_in, num_out))
        port_names = port_names or [None for _ in range(num_str_itfs)]
        while len(port_names) < num_str_itfs:
            port_names.append(None)
        self.port_names = port_names
        self.ports = [MidiPortInterface(i, i < num_in, i < num_out, num_str_itfs, self.port_names[i]) for i in range(num_str_itfs)]

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
        desc.interface(itf_num, # bInterfaceNumber (unique ID)
                       0,       # bNumEndpoints (no endpoints)
                       1,       # bInterfaceClass=AUDIO
                       1,       # bInterfaceSubClass=AUDIO_CONTROL
                       0,       # bInterfaceProtocol (unused)
                       0        # iInterface (index of string descriptor or 0 if none assigned)
                       )
        # Class-specific Audio Control header, points to all MIDI Streaming interfaces following
        bLength = 8 + (num_str_itfs := self.num_str_itfs)
        wTotalLength = 8 + num_str_itfs
        baInterfaceNr = list(range(itf_num + 1, itf_num + 1 + num_str_itfs))
        desc.pack('<BBBHHB' + 'B' * num_str_itfs, 
                  bLength,       # bLength (size of the descriptor in bytes)
                  0x24,          # bDescriptorType=CS_INTERFACE
                  1,             # bDescriptorSubType=MS_HEADER
                  0x0100,        # bcdADC (USB MIDI 1.0 specs)
                  wTotalLength,  # wTotalLength (total size of class specific descriptors)
                  num_str_itfs,     # bInCollection (number of streaming interfaces)
                  *baInterfaceNr # baInterfaceNr(1 to n) (assign MIDIStreming interfaces 1 to n)
              )
        itf_num += 1
        for i, port in enumerate(self.ports):
            port.desc_cfg(desc, itf_num + i, ep_num + i, strs)

    def num_itfs(self):
        return 1 + self.num_str_itfs

    def num_eps(self):
        return self.num_str_itfs

    def on_open(self):
        super().on_open()
        # Kick off any transfers that may have queued while the device was not open
        for p in self.ports:
            p.on_open()

class MidiPortInterface(Interface):
    '''Class providing one MIDIStreaming interface for one port'''

    def __init__(self, port_index, add_in, add_out, num_str_itfs, port_name=None):
        super().__init__()
        self.port_index = port_index
        self.add_in = add_in
        self.add_out = add_out
        self.num_str_itfs = num_str_itfs
        self.port_name = port_name
        self.ep_out = None
        self.ep_in = None
        self._rx_buffer = Buffer(_BUFFER_SIZE)
        self._tx_buffer = Buffer(_BUFFER_SIZE)
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

    def desc_cfg(self, desc, ms_if_num, ep_num, strs):
        in_emb_jack_id = 1 + (4 if _ADD_EXTERNAL_JACKS else 2) * (port := self.port_index)
        in_ext_jack_id = in_emb_jack_id + 1
        out_emb_jack_id = (in_ext_jack_id if _ADD_EXTERNAL_JACKS else in_emb_jack_id) + 1
        out_ext_jack_id = out_emb_jack_id + 1
        # MIDI Streaming interface
        bNumEndpoints = (add_in := self.add_in) + (add_out := self.add_out)
        desc.interface(ms_if_num,     # bInterfaceNumber (unique ID)
                       bNumEndpoints, # bNumEndpoints (number of MIDI endpoints assigned to this MIDI Streaming interface)
                       1,             # bInterfaceClass=AUDIO
                       3,             # bInterfaceSubClass=MIDISTREAMING
                       0,             # bInterfaceProtocol (unused)
                       0              # iInterface (index of string descriptor or 0 if none assigned)
                       )
        _pack = desc.pack
        # Class-specific MIDI Streaming header
        wTotalLength = 7 + 2 * (6 + 9) if _ADD_EXTERNAL_JACKS else 7 + 6 + 9
        _pack('<BBBHH',
              7,           # bLength (size of the descriptor in bytes)
              0x24,        # bDescriptorType=CS_INTERFACE
              1,           # bDescriptorSubType=MS_HEADER
              0x0100,      # bcdADC (USB MIDI 1.0 specs)
              wTotalLength # wTotalLength (total size of class specific descriptors)
              )
        # Embedded IN Jack (required - create dummy if no IN port is to be exposed)
        if (name := self.port_name) is None:
            iJack = 0
        else:
            iJack = len(strs)
            strs.append(name)
        _pack('<BBBBBB',
              6,              # bLength (size of the descriptor in bytes)
              0x24,           # bDescriptorType=CS_INTERFACE
              2,              # bDescriptorSubType=MIDI_IN_JACK
              1,              # bJackType=EMBEDDED
              in_emb_jack_id, # bJackID (unique ID)
              iJack           # iJack (index of string descriptor or 0 if none assigned)
              )
        # External IN Jack (create dummy if no IN port is to be exposed)
        if _ADD_EXTERNAL_JACKS:
            _pack('<BBBBBB',
                  6,              # bLength (size of the descriptor in bytes)
                  0x24,           # bDescriptorType=CS_INTERFACE
                  2,              # bDescriptorSubType=MIDI_IN_JACK
                  2,              # bJackType=EXTERNAL
                  in_ext_jack_id, # bJackID (unique ID)
                  0               # iJack (index of string descriptor or 0 if none assigned)
                  )
        # Embedded OUT Jack (required - create dummy if no OUT port is to be exposed)
        in_jack_id = in_ext_jack_id if _ADD_EXTERNAL_JACKS else in_emb_jack_id
        _pack('<BBBBBBBBB',
              9,               # bLength (size of the descriptor in bytes)
              0x24,            # bDescriptorType=CS_INTERFACE
              3,               # bDescriptorSubType=MIDI_OUT_JACK
              1,               # bJackType=EMBEDDED
              out_emb_jack_id, # bJackID (unique ID)
              1,               # bNrInputPins (number of input Pins on this MIDI OUT Jack)
              in_jack_id,      # baSourceID(1) (ID of the Entity to which the first Pin is connected)
              1,               # baSourcePIN(1) (output Pin number for the Entity to which the first Pin is connected)
              iJack           # iJack (index of string descriptor or 0 if none assigned)
              )
        # External OUT Jack (create dummy if no IN port is to be exposed)
        if _ADD_EXTERNAL_JACKS:
            _pack('<BBBBBBBBB',
                  9,               # bLength (size of the descriptor in bytes)
                  0x24,            # bDescriptorType=CS_INTERFACE
                  3,               # bDescriptorSubType=MIDI_OUT_JACK
                  2,               # bJackType=EXTERNAL
                  out_ext_jack_id, # bJackID (unique ID)
                  1,               # bNrInputPins (number of input Pins on this MIDI OUT Jack)
                  in_emb_jack_id,  # baSourceID(1) (ID of the Entity to which the first Pin is connected)
                  1,               # baSourcePIN(1) (output Pin number for the Entity to which the first Pin is connected)
                  0               # iJack (index of string descriptor or 0 if none assigned)
                  )
        # OUT Endpoint
        if add_in:
            self.ep_out = ep_num
            _pack('<BBBBHB',
                7,               # bLength (size of the descriptor in bytes)
                5,               # bDescriptorType=ENDPOINT
                ep_num,          # bEndpointAddress (0 to 15 with bit7=0 for OUT)
                2,               # bmAttributes (2 for Bulk, not shared; alternative: 3 for Interval)
                _EP_PACKET_SIZE, # wMaxPacketSize
                0                # bInterval (ignored for Bulk - set to 0; alternative: 1 for Interval)
                )
            _pack('<BBBBB',
                5,             # bLength (size of the descriptor in bytes)
                0x25,          # bDescriptorType=CS_ENDPOINT
                1,             # bDescriptorSubtype=MS_GENERAL
                1,             # bNumEmbMIDIJack (number of Embedded MIDI IN Jacks)
                in_emb_jack_id # baAssocJackID(1) (ID of the first associated Embedded MIDI IN Jack)
                )
        # IN Endpoint
        if add_out:
            self.ep_in = (ep_in := ep_num | 0x80)
            _pack('<BBBBHB',
                7,               # bLength (size of the descriptor in bytes)
                5,               # bDescriptorType=ENDPOINT
                ep_in,           # bEndpointAddress (0 to 15 with bit7=1 for IN: 128 to 143)
                2,               # bmAttributes (2 for Bulk, not shared; alternative: 3 for Interval)
                _EP_PACKET_SIZE, # wMaxPacketSize
                0                # bInterval (ignored for Bulk - set to 0; alternative: 1 for Interval)
                )
            _pack('<BBBBB',
                5,              # bLength (size of the descriptor in bytes)
                0x25,           # bDescriptorType=CS_ENDPOINT
                1,              # bDescriptorSubtype=MS_GENERAL
                1,              # bNumEmbMIDIJack (number of Embedded MIDI IN Jacks)
                out_emb_jack_id # baAssocJackID(1) (ID of the first associated Embedded MIDI OUT Jack)
                )

    def on_open(self):
        super().on_open()
        # Kick off any transfers that may have queued while the device was not open
        self._tx_xfer()
        self._rx_xfer()

    def _tx_xfer(self):
        '''Keep an active IN transfer to send data to the host, whenever there is data to send'''
        _buffer = self._tx_buffer
        if (ep_in := self.ep_in) is not None and self.is_open() and not self.xfer_pending(ep_in) and _buffer.readable():
            self.submit_xfer(ep_in, _buffer.pend_read(), self._tx_cb)

    def _tx_cb(self, ep, res, num_bytes):
        if res == 0:
            self._tx_buffer.finish_read(num_bytes)
        self._tx_xfer()

    def _rx_xfer(self):
        '''Keep an active OUT transfer to receive MIDI events from the host'''
        _buffer = self._rx_buffer
        if (ep_out := self.ep_out) is not None and self.is_open() and not self.xfer_pending(ep_out) and _buffer.writable():
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