''' Multi-port USB MIDI 2.0 library for MicroPython, accepting MIDI 1.0 Protocol messages, with fallback to USB MIDI 1.0 with multiple Cables

    Requires at least Windows 24H2 (expected to be released in autumn 2025), Linux kernel 6.5 or macOS 11

    This library is in a very preliminary testing phase and further development might introduce breaking changes

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

_BUFFER_SIZE        = const(64)
_EP_PACKET_SIZE     = const(64)
_ADD_EXTERNAL_JACKS = const(False) # External Jacks are optional
_MAX_CABLES         = const(16)   # USB MIDI 1.0: up to 16 cables per Endpoint
_MAX_GROUPS         = const(16)    # USB MIDI 2.0: up to 16 Groups per Endpoint

class MidiMulti(Interface):
    '''USB MIDI 2.0 device class supporting up to 16 MIDI ports in the form of groups'''

    def __init__(self, num_ports=1, port_names=None, callback=None):
        if not 1 <= num_ports <= _MAX_GROUPS:
            raise ValueError(f'num_ports ({num_ports}) should be between 1 and {_MAX_GROUPS}')
        super().__init__()
        self.num_ports = num_ports
        port_names = port_names or [None for _ in range(num_ports)]
        if (n := len(port_names)) > num_ports:
            del port_names[num_ports - n:]
        while len(port_names) < num_ports:
            port_names.append(None)
        self.port_names = port_names
        self._in_callback = callback
        self.ep_out = None
        self.ep_in = None
        self._rx_buffer = Buffer(_BUFFER_SIZE)
        self._tx_buffer = Buffer(_BUFFER_SIZE)

    # Helper functions for sending common MIDI messages

    def note_on(self, group, channel, note, velocity=0x40):
        self.send_ump(bytes((0x20 | (group & 0x0F), 0x90 | channel, note, velocity)))

    def note_off(self, group, channel, note, velocity=0x40):
        self.send_ump(bytes((0x20 | (group & 0x0F), 0x80 | channel, note, velocity)))

    def control_change(self, group, channel, controller, value):
        self.send_ump(bytes((0x20 | (group & 0x0F), 0xB0 | channel, controller, value)))

    def send_ump(self, ump_bytes):
        '''Queue a UMP (Universal MIDI Packet) to be sent to the host; takes a group number (port) and a 4, 8, 12 or 16 bytes UMP; returns
        False if failed due to the TX buffer being full'''
        _buffer = self._tx_buffer
        w = _buffer.pend_write()
        if len(w) < (n := len(ump_bytes)):
            return False # TX buffer full
        w[:n] = ump_bytes
        _buffer.finish_write(n)
        self._tx_xfer()
        return True

    def desc_cfg(self, desc, itf_num, ep_num, strs):
        # Interface Association Descriptor
        desc.interface_assoc(
            bFirstInterface   = itf_num,
            bInterfaceCount   = 2,
            bFunctionClass    = 1,
            bFunctionSubClass = 3,
            bFunctionProtocol = 0,
            iFunction         = 0
        )
        _interface = desc.interface
        # Audio Control interface
        _interface(
            bInterfaceNumber   = itf_num, # Unique ID
            bAlternateSetting  = 0,       # Alternate Setting index
            bNumEndpoints      = 0,       # No endpoints
            bInterfaceClass    = 1,       # AUDIO
            bInterfaceSubClass = 1,       # AUDIO_CONTROL
            bInterfaceProtocol = 0,       # Unused
            iInterface         = 0        # Index of string descriptor or 0 if none assigned
        )
        _pack = desc.pack
        _pack('<BBBHHBB', 
              9,          # bLength (size of the descriptor in bytes)
              0x24,       # bDescriptorType=CS_INTERFACE
              1,          # bDescriptorSubType=MS_HEADER
              0x0100,     # bcdADC=MS_MIDI_1_0
              9,          # wTotalLength (total size of class specific descriptors)
              1,          # bInCollection (number of streaming interfaces)
              itf_num + 1 # baInterfaceNr(1) (assign MIDIStreaming interface 1)
        )
        # MIDI Streaming interface for Alternate Setting 0 (USB MIDI 1.0)
        _interface(
            bInterfaceNumber   = itf_num + 1, # Unique ID
            bAlternateSetting  = 0,           # Alternate Setting index
            bNumEndpoints      = 2,           # Number of MIDI endpoints assigned to this MIDI Streaming interface
            bInterfaceClass    = 1,           # AUDIO
            bInterfaceSubClass = 3,           # MIDISTREAMING
            bInterfaceProtocol = 0,           # Unused
            iInterface         = 0            # Index of string descriptor or 0 if none assigned
        )
        # Class-specific MIDI Streaming interface header for Alternate Setting 0 (USB MIDI 1.0)
        num_ports = self.num_ports
        wTotalLength = 33 + num_ports * 32 if _ADD_EXTERNAL_JACKS else 33 + num_ports * 14
        _pack('<BBBHH',
              7,           # bLength (size of the descriptor in bytes)
              0x24,        # bDescriptorType=CS_INTERFACE
              1,           # bDescriptorSubType=MS_HEADER
              0x0100,      # bcdADC=MS_MIDI_1_0
              wTotalLength # wTotalLength (total size of class specific descriptors)
        )
######
        # IN and OUT Jacks for each virtual IN and OUT Cable (USB MIDI 1.0)
        in_emb_jack_ids = []
        out_emb_jack_ids = []
        jack_id = 1
        for i, name in enumerate(port_names := self.port_names):
            # Embedded IN Jack for each virtual OUT Cable (USB MIDI 1.0)
            if name is None:
                iJack = 0
            else:
                iJack = len(strs)
######
                strs.append(name + ' 1.0')
            _pack('<BBBBBB',
                  6,                           # bLength (size of the descriptor in bytes)
                  0x24,                        # bDescriptorType=CS_INTERFACE
                  2,                           # bDescriptorSubType=MIDI_IN_JACK
                  1,                           # bJackType=EMBEDDED
                  (in_emb_jack_id := jack_id), # bJackID (unique ID)
                  iJack                        # iJack (index of string descriptor or 0 if none assigned)
            )
            in_emb_jack_ids.append(jack_id)
            jack_id += 1
            # External IN Jack for each virtual OUT Cable (USB MIDI 1.0)
            if _ADD_EXTERNAL_JACKS:
                _pack('<BBBBBB',
                    6,                           # bLength (size of the descriptor in bytes)
                    0x24,                        # bDescriptorType=CS_INTERFACE
                    2,                           # bDescriptorSubType=MIDI_IN_JACK
                    2,                           # bJackType=EXTERNAL
                    (in_ext_jack_id := jack_id), # bJackID (unique ID)
                    0                            # iJack (index of string descriptor or 0 if none assigned)
                )
                jack_id += 1
            # Embedded OUT Jack for each virtual OUT Cable (USB MIDI 1.0)
            in_jack_id = in_ext_jack_id if _ADD_EXTERNAL_JACKS else in_emb_jack_id
            _pack('<BBBBBBBBB',
                  9,          # bLength (size of the descriptor in bytes)
                  0x24,       # bDescriptorType=CS_INTERFACE
                  3,          # bDescriptorSubType=MIDI_OUT_JACK
                  1,          # bJackType=EMBEDDED
                  jack_id,    # bJackID (unique ID)
                  1,          # bNrInputPins (number of input Pins on this MIDI OUT Jack)
                  in_jack_id, # baSourceID(1) (ID of the Entity to which the first Pin is connected)
                  1,          # baSourcePIN(1) (output Pin number for the Entity to which the first Pin is connected)
                  iJack       # iJack (index of string descriptor or 0 if none assigned)
            )
            out_emb_jack_ids.append(jack_id)
            jack_id += 1
            # External OUT Jack for each virtual OUT Cable (USB MIDI 1.0)
            if _ADD_EXTERNAL_JACKS:
                _pack('<BBBBBBBBB',
                    9,              # bLength (size of the descriptor in bytes)
                    0x24,           # bDescriptorType=CS_INTERFACE
                    3,              # bDescriptorSubType=MIDI_OUT_JACK
                    2,              # bJackType=EXTERNAL
                    jack_id,        # bJackID (unique ID)
                    1,              # bNrInputPins (number of input Pins on this MIDI OUT Jack)
                    in_emb_jack_id, # baSourceID(1) (ID of the Entity to which the first Pin is connected)
                    1,              # baSourcePIN(1) (output Pin number for the Entity to which the first Pin is connected)
                    0               # iJack (index of string descriptor or 0 if none assigned)
                )
                jack_id += 1
        # Single shared OUT Endpoint (USB MIDI 1.0)
        self.ep_out = ep_num
        _pack('<BBBBHB',
              9,               # bLength (size of the descriptor in bytes)
              5,               # bDescriptorType=ENDPOINT
              ep_num,          # bEndpointAddress (0 to 15 with bit7=0 for OUT)
              2,               # bmAttributes (2 for Bulk, not shared; alternative: 3 for Interval)
              _EP_PACKET_SIZE, # wMaxPacketSize
              0,               # bInterval (ignored for Bulk - set to 0; alternative: 1 for Interval)
              0,               # bRefresh (unused)
              0                # bSynchAddress (unused)
        )
        _pack('<BBBB' + num_ports * 'B',
              4 + num_ports,   # bLength (size of the descriptor in bytes)
              0x25,            # bDescriptorType=CS_ENDPOINT
              1,               # bDescriptorSubtype=MS_GENERAL
              num_ports,       # bNumEmbMIDIJack (number of Embedded MIDI IN Jacks)
              *in_emb_jack_ids # baAssocJackID(1 to n) (IDs of the associated Embedded MIDI IN Jacks)
        )
        # Single shared IN Endpoint (USB MIDI 1.0)
        self.ep_in = (ep_in := ep_num | 0x80)
        _pack('<BBBBHB',
              9,               # bLength (size of the descriptor in bytes)
              5,               # bDescriptorType=ENDPOINT
              ep_in,           # bEndpointAddress (0 to 15 with bit7=1 for IN: 128 to 143)
              2,               # bmAttributes (2 for Bulk, not shared; alternative: 3 for Interval)
              _EP_PACKET_SIZE, # wMaxPacketSize
              0,               # bInterval (ignored for Bulk - set to 0; alternative: 1 for Interval)
###### needed?
              0,               # bRefresh (unused)
              0                # bSynchAddress (unused)
        )
        _pack('<BBBB' + num_ports * 'B',
              4 + num_ports,    # bLength (size of the descriptor in bytes)
              0x25,             # bDescriptorType=CS_ENDPOINT
              1,                # bDescriptorSubtype=MS_GENERAL
              num_ports,          # bNumEmbMIDIJack (number of Embedded MIDI IN Jacks)
              *out_emb_jack_ids # baAssocJackID(1 to n) (IDs of the associated Embedded MIDI OUT Jacks)
        )
######
        # MIDI Streaming interface for Alternate Setting 1 (USB MIDI 2.0)
        _interface(
            bInterfaceNumber   = itf_num + 1, # Unique ID
            bAlternateSetting  = 1,           # Alternate Setting index
            bNumEndpoints      = 2,           # Number of MIDI endpoints assigned to this MIDI Streaming interface
            bInterfaceClass    = 1,           # AUDIO
            bInterfaceSubClass = 3,           # MIDISTREAMING
            bInterfaceProtocol = 0,           # Unused
            iInterface         = 0            # Index of string descriptor or 0 if none assigned
        )
        # Class-specific MIDI Streaming interface header for Alternate Setting 1 (USB MIDI 2.0)
######
        wTotalLength = 17 + num_ports * 18
        _pack('<BBBHH',
              7,      # bLength (size of the descriptor in bytes)
              0x24,   # bDescriptorType=CS_INTERFACE
              1,      # bDescriptorSubType=MS_HEADER
              0x0200, # bcdADC=MS_MIDI_2_0
######
            #   7       # wTotalLength (needs to match bLength)
              wTotalLength # wTotalLength (total size of class specific descriptors)
        )
        # Groups for each IN and OUT Port (USB MIDI 2.0)
        for i, name in enumerate(port_names):
            # Embedded IN Jack for each virtual OUT Cable (required - create dummy if no OUT port is to be exposed)
            if name is None:
                iBlockItem = 0
            else:
                iBlockItem = len(strs)
######
                strs.append(name + ' 2.0')
            wTotalLength = 5 + num_ports * 13
            _pack('<BBBH',
                  5,           # bLength (size of the descriptor in bytes)
                  0x26,        # bDescriptorType=CS_GR_TRM_BLOCK
                  1,           # bDescriptorSubType=GR_TRM_BLOCK_HEADER
                  wTotalLength # wTotalLength (total size of class specific descriptors)
            )
            _pack('<BBBBBBBBBHH',
                  13,         # bLength (size of the descriptor in bytes)
                  0x26,       # bDescriptorType=CS_GR_TRM_BLOCK
                  2,          # bDescriptorSubType=GR_TRM_BLOCK
                  i + 1,      # bGrpTrmBlkID (unique ID)                
                  0,          # bGrpTrmBlkType=BIDIRECTIONAL (alternatives: INPUT_ONLY = 1 OUTPUT_ONLY = 2)
                  0,          # nGroupTrm (first member Group Terminal in this block; must be in range 0 to 15)
                  num_ports,  # nNumGroupTrm (number of member Group Terminals spanned; must be in range 1 to 15 - nGroupTrm)
                  iBlockItem, # iBlockItem (index of string descriptor or 0 if none assigned???)
######
                #   3,          # bMIDIProtocol=MIDI_1_0_UP_TO_128_BITS (altenative: MIDI_1_0_UP_TO_64_BITS = 1)
0,
                  0,          # wMaxInputBandwidth (0 for unknown or not fixed, alternative: 1 for rounded version of 31.25kb/s)
                  0,          # wMaxOutputBandwidth (0 for unknown or not fixed, alternative: 1 for rounded version of 31.25kb/s)
            )
        # OUT Endpoint (USB MIDI 2.0)
######
        # self.ep_out = ep_num
        _pack('<BBBBHB',
              7,               # bLength (size of the descriptor in bytes)
              5,               # bDescriptorType=ENDPOINT
              ep_num,          # bEndpointAddress (0 to 15 with bit7=0 for OUT)
              2,               # bmAttributes (2 for Bulk, not shared; alternative: 3 for Interval)
              _EP_PACKET_SIZE, # wMaxPacketSize
              0                # bInterval (ignored for Bulk - set to 0; alternative: 1 for Interval)
        )
        _pack('<BBBB' + num_ports * 'B',
              4 + num_ports,                     # bLength (size of the descriptor in bytes)
              0x25,                              # bDescriptorType=CS_ENDPOINT
              2,                                 # bDescriptorSubtype=MS_GENERAL_2_0
              num_ports,                         # bNumGrpTrmBlock (number of Group Terminal Blocks)
              *[i + 1 for i in range(num_ports)] # baAssocGrpTrmBlkID(1 to n) (IDs of the associated Group Terminal Blocks)
        )
        # IN Endpoint (USB MIDI 2.0)
######
        # self.ep_in = (ep_in := ep_num | 0x80)
        _pack('<BBBBHB',
              7,               # bLength (size of the descriptor in bytes)
              5,               # bDescriptorType=ENDPOINT
              ep_in,           # bEndpointAddress (0 to 15 with bit7=1 for IN: 128 to 143)
              2,               # bmAttributes (2 for Bulk, not shared; alternative: 3 for Interval)
              _EP_PACKET_SIZE, # wMaxPacketSize
              0                # bInterval (ignored for Bulk - set to 0; alternative: 1 for Interval)
        )
        _pack('<BBBB' + num_ports * 'B',
              4 + num_ports,   # bLength (size of the descriptor in bytes)
              0x25,            # bDescriptorType=CS_ENDPOINT
              2,               # bDescriptorSubtype=MS_GENERAL_2_0
              num_ports,       # bNumGrpTrmBlock (number of Group Terminal Blocks)
              *[i + 1 for i in range(num_ports)] # baAssocGrpTrmBlkID(1 to n) (IDs of the associated Group Terminal Blocks)
        )

    def num_itfs(self):
        return 2

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
        _buffer = self._rx_buffer
        m = _buffer.pend_read()
        _callback = self._in_callback
        i = 0
        while i <= len(m) - 4:
            ump_len = 4 * ((m[i] & 0x0F) + 1)
            if ump_len > len(m) - i:
                break # incomplete packet
            try:
                _callback(m[i:i + ump_len]) # type: ignore
            except:
                pass
            i += ump_len
        _buffer.finish_read(i)