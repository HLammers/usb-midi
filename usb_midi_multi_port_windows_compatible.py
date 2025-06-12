"""
Flexible Windows-compatible USB MIDI 1.0 multi-port implementation.

- Each port is a pair of Jacks and endpoints within a single MIDIStreaming interface.
- Each port can have a custom interface name (i.e., for DAW display as "Port 1", "Port 2" etc.).
- Number of ports is configurable.
- Ensures cross-platform compatibility (Windows, macOS, Linux).

NOTE: Windows only supports multiple MIDI "ports" if each is presented as a separate interface
with its own endpoints and unique string descriptor for the interface name.

Integrate the code with your USB stack as needed.
"""

from micropython import const

_INTERFACE_CLASS_AUDIO = const(0x01)
_INTERFACE_SUBCLASS_AUDIO_CONTROL = const(0x01)
_INTERFACE_SUBCLASS_AUDIO_MIDISTREAMING = const(0x03)
_STD_DESC_AUDIO_ENDPOINT_LEN = const(9)
_CLASS_DESC_ENDPOINT_LEN = const(5)
_JACK_TYPE_EMBEDDED = const(0x01)
_JACK_TYPE_EXTERNAL = const(0x02)
_JACK_IN_DESC_LEN = const(6)
_JACK_OUT_DESC_LEN = const(9)
EP_MIDI_PACKET_SIZE = 32

class MidiMultiPort:
    def __init__(self, num_ports=2, product_name="TestMIDI", ac_interface_name="MIDI Control", port_names=None):
        self.num_ports = num_ports
        self.product_name = product_name
        self.ac_interface_name = ac_interface_name
        if port_names is None:
            self.port_names = [f"MIDI Port {i+1}" for i in range(num_ports)]
        else:
            self.port_names = port_names

    def desc_cfg(self, desc, itf_start, ep_start, strs):
        ac_if_str_idx = self._get_str_idx(strs, self.ac_interface_name)
        # AudioControl interface (interface 0)
        desc.interface(itf_start, 0, _INTERFACE_CLASS_AUDIO, _INTERFACE_SUBCLASS_AUDIO_CONTROL, ac_if_str_idx)
        desc.pack("<BBBHHBB", 9, 0x24, 0x01, 0x0100, 0x0009, self.num_ports, itf_start + 1)

        jack_id = 1
        ep = ep_start
        # For each port: Make a separate MIDIStreaming interface
        for port_idx in range(self.num_ports):
            ms_if_str_idx = self._get_str_idx(strs, self.port_names[port_idx])
            # MIDIStreaming interface for this port
            desc.interface(itf_start + 1 + port_idx, 2, _INTERFACE_CLASS_AUDIO, _INTERFACE_SUBCLASS_AUDIO_MIDISTREAMING, ms_if_str_idx)
            # Class-specific MS interface header
            class_len = 7 + 2 * _JACK_IN_DESC_LEN + 2 * _JACK_OUT_DESC_LEN
            desc.pack("<BBBHH", 7, 0x24, 0x01, 0x0100, class_len)
            # Jacks: Embedded IN, External IN, Embedded OUT, External OUT
            emb_in_id  = jack_id
            ext_in_id  = jack_id + 1
            emb_out_id = jack_id + 2
            ext_out_id = jack_id + 3
            desc.pack("<BBBBBB", _JACK_IN_DESC_LEN, 0x24, 0x02, _JACK_TYPE_EMBEDDED, emb_in_id, 0)
            desc.pack("<BBBBBB", _JACK_IN_DESC_LEN, 0x24, 0x02, _JACK_TYPE_EXTERNAL, ext_in_id, 0)
            desc.pack("<BBBBBBBBB", _JACK_OUT_DESC_LEN, 0x24, 0x03, _JACK_TYPE_EMBEDDED, emb_out_id, 1, ext_in_id, 1, 0)
            desc.pack("<BBBBBBBBB", _JACK_OUT_DESC_LEN, 0x24, 0x03, _JACK_TYPE_EXTERNAL, ext_out_id, 1, emb_in_id, 1, 0)
            # Endpoints
            ep_in  = 0x81 + port_idx
            ep_out = 0x01 + port_idx
            desc.pack("<BBBBHBBB", _STD_DESC_AUDIO_ENDPOINT_LEN, 0x05, ep_in, 2, EP_MIDI_PACKET_SIZE, 0, 0, 0)
            desc.pack("<BBBBB", _CLASS_DESC_ENDPOINT_LEN, 0x25, 0x01, 1, emb_out_id)
            desc.pack("<BBBBHBBB", _STD_DESC_AUDIO_ENDPOINT_LEN, 0x05, ep_out, 2, EP_MIDI_PACKET_SIZE, 0, 0, 0)
            desc.pack("<BBBBB", _CLASS_DESC_ENDPOINT_LEN, 0x25, 0x01, 1, emb_in_id)
            # Prepare for next port
            jack_id += 4

    def num_itfs(self):
        return 1 + self.num_ports  # 1 AudioControl + N MIDIStreaming

    def num_eps(self):
        return self.num_ports * 2  # Each port = 2 endpoints (IN and OUT)

    def _get_str_idx(self, strs, s):
        if not s:
            return 0
        if s in strs:
            return strs.index(s) + 1
        strs.append(s)
        return len(strs)

# Dummy Descriptor class for test printing
class Descriptor:
    def __init__(self, buf):
        self.b = buf
        self.o = 0
    def pack(self, fmt, *args):
        import struct
        s = struct.pack(fmt, *args)
        self.b[self.o:self.o + len(s)] = s
        self.o += len(s)
    def interface(self, num, eps, cls, subcls, iInterface=0):
        # Standard interface descriptor with iInterface
        self.pack("<BBBBBBBBB", 9, 0x04, num, 0, eps, cls, subcls, 0x00, iInterface)

if __name__ == "__main__":
    NUM_PORTS = 2
    PRODUCT_NAME = "TestMIDI"
    AC_IF_NAME = "Test MIDI Control"
    PORT_NAMES = ["MIDI Port A", "MIDI Port B"]

    midi = MidiMultiPort(num_ports=NUM_PORTS, product_name=PRODUCT_NAME, ac_interface_name=AC_IF_NAME, port_names=PORT_NAMES)
    import usb.device.core
    desc = usb.device.core.Descriptor(bytearray(512))
    # desc = Descriptor(bytearray(512))
    strs = [PRODUCT_NAME, AC_IF_NAME] + PORT_NAMES
    # Config header: wTotalLength will be filled later
    desc.pack("<BBHBBBBB", 9, 2, 0, midi.num_itfs(), 1, 0, 0x80, 0x32)

    # 🟢 Add IAD here!
    desc.pack("<BBBBBBBB", 8, 0x0B, 0, 3, 0x01, 0x00, 0x00, 0)

    midi.desc_cfg(desc, 0, 1, strs)
    wTotalLength = desc.o
    desc.b[2] = wTotalLength & 0xFF
    desc.b[3] = (wTotalLength >> 8) & 0xFF

    print("Config descriptor header:", list(desc.b[:9]))
    print("Descriptor length:", desc.o)
    print("Descriptor hex:", desc.b[:desc.o].hex())
    print("Descriptor bytes:", list(desc.b[:desc.o]))
    print("String descriptors:", strs)

    import time
    time.sleep_ms(1000)

    dev = usb.device.get()
    dev.init(midi, manufacturer_str="TestMaker", product_str="TestMIDI", serial_str="123456")