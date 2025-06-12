"""
Test file for a cross-platform class-compliant USB MIDI 1.0 device
with two virtual cables ("ports") in a single MIDIStreaming interface.

- Enumerates as a MIDI device with two cables/ports on Windows, macOS, and Linux.
- Each cable gets its own IN and OUT endpoint.
- All cables share the same product/interface name ("TestMIDI").
- Prints out the raw descriptor for inspection.

You need a USB device stack (like TinyUSB, MicroPython, or a suitable simulation) 
that supports direct descriptor construction and USB initialization. 
"""

from usb.device.core import Interface
import usb.device
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

EP1_OUT = 0x01
EP1_IN  = 0x81
EP2_OUT = 0x02
EP2_IN  = 0x82
EP_MIDI_PACKET_SIZE = 32
NUM_CABLES = 2

class MidiTwoCable:
    def desc_cfg(self, desc, itf_num, ep_num, strs):
        # AudioControl interface (interface 0)
        desc.interface(itf_num, 0, _INTERFACE_CLASS_AUDIO, _INTERFACE_SUBCLASS_AUDIO_CONTROL)
        desc.pack("<BBBHHBB", 9, 0x24, 0x01, 0x0100, 0x0009, 1, itf_num + 1)

        # MIDIStreaming Interface (interface 1)
        midi_if1 = itf_num + 1
        desc.interface(midi_if1, 2 * NUM_CABLES, _INTERFACE_CLASS_AUDIO, _INTERFACE_SUBCLASS_AUDIO_MIDISTREAMING)
        total_class_len = 7 + 2 * NUM_CABLES * _JACK_IN_DESC_LEN + 2 * NUM_CABLES * _JACK_OUT_DESC_LEN
        desc.pack("<BBBHH", 7, 0x24, 0x01, 0x0100, total_class_len)
        # Jacks: Embedded IN, External IN, Embedded OUT, External OUT for each cable
        for i in range(NUM_CABLES):
            emb_in_id = 1 + i
            ext_in_id = 1 + NUM_CABLES + i
            emb_out_id = 1 + 2 * NUM_CABLES + i
            ext_out_id = 1 + 3 * NUM_CABLES + i
            # Embedded IN, External IN
            desc.pack("<BBBBBB", _JACK_IN_DESC_LEN, 0x24, 0x02, _JACK_TYPE_EMBEDDED, emb_in_id, 0)
            desc.pack("<BBBBBB", _JACK_IN_DESC_LEN, 0x24, 0x02, _JACK_TYPE_EXTERNAL, ext_in_id, 0)
            # Embedded OUT, External OUT
            desc.pack("<BBBBBBBBB", _JACK_OUT_DESC_LEN, 0x24, 0x03, _JACK_TYPE_EMBEDDED, emb_out_id, 1, ext_in_id, 1, 0)
            desc.pack("<BBBBBBBBB", _JACK_OUT_DESC_LEN, 0x24, 0x03, _JACK_TYPE_EXTERNAL, ext_out_id, 1, emb_in_id, 1, 0)
        # Endpoints for each cable
        for i in range(NUM_CABLES):
            ep_in = 0x81 + i
            ep_out = 0x01 + i
            emb_in_id = 1 + i
            emb_out_id = 1 + 2 * NUM_CABLES + i
            desc.pack("<BBBBHBBB", _STD_DESC_AUDIO_ENDPOINT_LEN, 0x05, ep_in, 2, EP_MIDI_PACKET_SIZE, 0, 0, 0)
            desc.pack("<BBBBB", _CLASS_DESC_ENDPOINT_LEN, 0x25, 0x01, 1, emb_out_id)
            desc.pack("<BBBBHBBB", _STD_DESC_AUDIO_ENDPOINT_LEN, 0x05, ep_out, 2, EP_MIDI_PACKET_SIZE, 0, 0, 0)
            desc.pack("<BBBBB", _CLASS_DESC_ENDPOINT_LEN, 0x25, 0x01, 1, emb_in_id)

    def num_itfs(self):
        return 2

    def num_eps(self):
        return 2 * NUM_CABLES


if __name__ == "__main__":
    midi = MidiTwoCable()
    import usb.device.core
    desc = usb.device.core.Descriptor(bytearray(512))
    # Config header: wTotalLength will be filled later
    desc.pack("<BBHBBBBB", 9, 2, 0, midi.num_itfs(), 1, 0, 0x80, 0x32)
    midi.desc_cfg(desc, 0, 1, [])
    wTotalLength = desc.o
    desc.b[2] = wTotalLength & 0xFF
    desc.b[3] = (wTotalLength >> 8) & 0xFF

    print("Config descriptor header:", list(desc.b[:9]))
    print("Descriptor length:", desc.o)
    print("Descriptor hex:", desc.b[:desc.o].hex())
    print("Descriptor bytes:", list(desc.b[:desc.o]))

    import time
    time.sleep_ms(1000)

    dev = usb.device.get()
    dev.init(midi, manufacturer_str="TestMaker", product_str="TestMIDI", serial_str="123456")