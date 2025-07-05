''' Multi-port USB MIDI 1.0 library for MicroPython based on multiple virtual Cables approach

    - Multiple MIDI ports set up this way are recognized by Windows (if builtin_driver=False) and Linux and shoud work on macOS as well (not
      tested)
    - An asymmetric approach (unequal number of IN and OUT ports) works with Linux, but not with Windows (not tested with macOS)
    - Port names are ignored by Windows, but shown by Linux and might be shown by macOS (not tested)
    - Linux recognizes different port names assigned to IN and OUT Jacks, but is very inconsistent which one of the two to use (one program
      uses the IN names for IN and OUT, while another uses the OUT names for both), so it is better to assign the same name to IN and OUT
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

_BUFFER_SIZE        = const(64)
_EP_PACKET_SIZE     = const(64)
_ADD_EXTERNAL_JACKS = const(False) # External Jacks are optional
_MAX_CABLES         = const(16)   # USB MIDI 1.0: up to 16 cables per Endpoint

class MidiMulti(Interface):
    '''USB MIDI 1.0 device class supporting up to 16 MIDI ports in the form of virtual MIDI IN and OUT cables'''

    def __init__(self, num_in=1, num_out=1, port_names=None, in_callback=None):
        if not 1 <= num_in <= _MAX_CABLES:
            raise ValueError(f'num_in ({num_in}) must be >= 1 and <= {_MAX_CABLES}')
        if not 1 <= num_out <= _MAX_CABLES:
            raise ValueError(f'num_out ({num_out}) must be >= 1 and <= {_MAX_CABLES}')
        super().__init__()
        self.num_in = num_in
        self.num_out = num_out
        self.num_jack_sets = (num_jack_sets := max(num_in, num_out))
        port_names = port_names or [None for _ in range(num_in)]
        if (n := len(port_names)) > num_jack_sets:
            del port_names[num_jack_sets - n:]
        while len(port_names) < num_jack_sets:
            port_names.append(None)
        self.port_names = port_names
        self._in_callback = in_callback
        self.ep_out = None
        self.ep_in = None
        self._rx_buffer = Buffer(_BUFFER_SIZE)
        self._tx_buffer = Buffer(_BUFFER_SIZE)

    # Helper functions for sending common MIDI messages

    def note_on(self, cable, channel, note, velocity=0x40):
        self.send_event(cable, 0x9, 0x90 | channel, note, velocity)

    def note_off(self, cable, channel, note, velocity=0x40):
        self.send_event(cable, 0x8, 0x80 | channel, note, velocity)

    def control_change(self, cable, channel, controller, value):
        self.send_event(cable, 0xB, 0xB0 | channel, controller, value)

    def send_event(self, cable, cin, data_0, data_1=0, data_2=0):
        '''Queue a MIDI Event Packet to be sent to the host; takes a Cable number (port), a USB-MIDI Code Index Number (CIN) and up to three
        MIDI data bytes; returns False if failed due to the TX buffer being full'''
        _buffer = self._tx_buffer
        w = _buffer.pend_write()
        if len(w) < 4:
            return False # TX buffer full
        status = (cable << 4) | cin # First 4 bits: Cable, second 4 bits: CIN
        w[:4] = bytes((status, data_0, data_1, data_2))
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
        _interface = desc.interface
        # Audio Control interface
        _interface(
            bInterfaceNumber   = itf_num, # Unique ID
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
              0x0100,     # bcdADC (USB MIDI 1.0 specs)
              9,          # wTotalLength (total size of class specific descriptors)
              1,          # bInCollection (number of streaming interfaces)
              itf_num + 1 # baInterfaceNr(1) (assign MIDIStreaming interface 1)
        )
        # MIDI Streaming interface
        _interface(
            bInterfaceNumber   = itf_num + 1, # Unique ID
            bNumEndpoints      = 2,           # Number of MIDI endpoints assigned to this MIDI Streaming interface
            bInterfaceClass    = 1,           # AUDIO
            bInterfaceSubClass = 3,           # MIDISTREAMING
            bInterfaceProtocol = 0,           # Unused
            iInterface         = 0            # Index of string descriptor or 0 if none assigned
        )
        # Class-specific MIDI Streaming interface header
        wTotalLength = 7 + 2 * self.num_jack_sets * (6 + 9) if _ADD_EXTERNAL_JACKS else 7 + self.num_jack_sets * (6 + 9)
        _pack('<BBBHH',
              7,           # bLength (size of the descriptor in bytes)
              0x24,        # bDescriptorType=CS_INTERFACE
              1,           # bDescriptorSubType=MS_HEADER
              0x0100,      # bcdADC (USB MIDI 1.0 specs)
              wTotalLength # wTotalLength (total size of class specific descriptors)
        )
        # IN and OUT Jacks for each virtual IN and OUT Cable
        in_emb_jack_ids = []
        out_emb_jack_ids = []
        num_in = self.num_in
        num_out = self.num_out
        jack_id = 1
        for i, name in enumerate(self.port_names):
            # Embedded IN Jack for each virtual OUT Cable (required - create dummy if no OUT port is to be exposed)
            if name is None:
                iJack = 0
            else:
                iJack = len(strs)
                strs.append(name)
            _pack('<BBBBBB',
                  6,                           # bLength (size of the descriptor in bytes)
                  0x24,                        # bDescriptorType=CS_INTERFACE
                  2,                           # bDescriptorSubType=MIDI_IN_JACK
                  1,                           # bJackType=EMBEDDED
                  (in_emb_jack_id := jack_id), # bJackID (unique ID)
                  iJack                        # iJack (index of string descriptor or 0 if none assigned)
            )
            if i < num_in:
                in_emb_jack_ids.append(jack_id)
            jack_id += 1
            # External IN Jack for each virtual OUT Cable (create dummy if no OUT port is to be exposed)
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
            # Embedded OUT Jack for each virtual OUT Cable (required - create dummy if no OUT port is to be exposed)
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
            if i < num_out:
                out_emb_jack_ids.append(jack_id)
            jack_id += 1
            # External OUT Jack for each virtual OUT Cable (create dummy if no OUT port is to be exposed)
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
        # Single shared OUT Endpoint
        self.ep_out = ep_num
        _pack('<BBBBHB',
              7,               # bLength (size of the descriptor in bytes)
              5,               # bDescriptorType=ENDPOINT
              ep_num,          # bEndpointAddress (0 to 15 with bit7=0 for OUT)
              2,               # bmAttributes (2 for Bulk, not shared; alternative: 3 for Interval)
              _EP_PACKET_SIZE, # wMaxPacketSize
              0                # bInterval (ignored for Bulk - set to 0; alternative: 1 for Interval)
        )
        _pack('<BBBB' + num_in * 'B',
              4 + num_in,      # bLength (size of the descriptor in bytes)
              0x25,            # bDescriptorType=CS_ENDPOINT
              1,               # bDescriptorSubtype=MS_GENERAL
              num_in,          # bNumEmbMIDIJack (number of Embedded MIDI IN Jacks)
              *in_emb_jack_ids # baAssocJackID(1 to n) (IDs of the associated Embedded MIDI IN Jacks)
        )
        # Single shared IN Endpoint
        self.ep_in = (ep_in := ep_num | 0x80)
        _pack('<BBBBHB',
              7,               # bLength (size of the descriptor in bytes)
              5,               # bDescriptorType=ENDPOINT
              ep_in,           # bEndpointAddress (0 to 15 with bit7=1 for IN: 128 to 143)
              2,               # bmAttributes (2 for Bulk, not shared; alternative: 3 for Interval)
              _EP_PACKET_SIZE, # wMaxPacketSize
              0                # bInterval (ignored for Bulk - set to 0; alternative: 1 for Interval)
        )
        _pack('<BBBB' + num_out * 'B',
              4 + num_out,      # bLength (size of the descriptor in bytes)
              0x25,             # bDescriptorType=CS_ENDPOINT
              1,                # bDescriptorSubtype=MS_GENERAL
              num_out,          # bNumEmbMIDIJack (number of Embedded MIDI IN Jacks)
              *out_emb_jack_ids # baAssocJackID(1 to n) (IDs of the associated Embedded MIDI OUT Jacks)
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
            cable = m[i] >> 4
            try:
                _callback(cable, m[i:i + 4]) # type: ignore
            except:
                pass
            i += 4
        _buffer.finish_read(i)