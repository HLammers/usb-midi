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

class MidiTwoCable(Interface):
    def desc_cfg(self, desc, itf_num, ep_num, strs):
        # AudioControl interface (interface 0)
        desc.interface(itf_num, 0, _INTERFACE_CLASS_AUDIO, _INTERFACE_SUBCLASS_AUDIO_CONTROL)
        desc.pack("<BBBHHBB", 9, 0x24, 0x01, 0x0100, 0x0009, 1, itf_num + 1)

        # MIDIStreaming Interface (interface 1)
        midi_if1 = itf_num + 1
        desc.interface(midi_if1, 4, _INTERFACE_CLASS_AUDIO, _INTERFACE_SUBCLASS_AUDIO_MIDISTREAMING)  # 4 endpoints
        total_class_len = (
            7 + 4*_JACK_IN_DESC_LEN + 4*_JACK_OUT_DESC_LEN
        )
        desc.pack("<BBBHH", 7, 0x24, 0x01, 0x0100, total_class_len)
        # Jacks: 2 Embedded IN, 2 External IN, 2 Embedded OUT, 2 External OUT
        # Jack IDs: 1,2,3,4,5,6,7,8 (all unique)
        # 1: Embedded IN 1, 2: Embedded IN 2, 3: External IN 1, 4: External IN 2,
        # 5: Embedded OUT 1, 6: Embedded OUT 2, 7: External OUT 1, 8: External OUT 2

        # IN Jacks (for OUT endpoints)
        _jack_in_desc(desc, _JACK_TYPE_EMBEDDED, 1)
        _jack_in_desc(desc, _JACK_TYPE_EMBEDDED, 2)
        _jack_in_desc(desc, _JACK_TYPE_EXTERNAL, 3)
        _jack_in_desc(desc, _JACK_TYPE_EXTERNAL, 4)

        # OUT Jacks (for IN endpoints)
        _jack_out_desc(desc, _JACK_TYPE_EMBEDDED, 5, 3, 1)  # Embedded OUT 1 <- External IN 1
        _jack_out_desc(desc, _JACK_TYPE_EMBEDDED, 6, 4, 1)  # Embedded OUT 2 <- External IN 2
        _jack_out_desc(desc, _JACK_TYPE_EXTERNAL, 7, 1, 1)  # External OUT 1 <- Embedded IN 1
        _jack_out_desc(desc, _JACK_TYPE_EXTERNAL, 8, 2, 1)  # External OUT 2 <- Embedded IN 2

        # Endpoints for cable 1
        _audio_endpoint(desc, EP1_IN)    # IN
        _midi_class_ep(desc, 5)          # Associated with Embedded OUT 1
        _audio_endpoint(desc, EP1_OUT)   # OUT
        _midi_class_ep(desc, 1)          # Associated with Embedded IN 1

        # Endpoints for cable 2
        _audio_endpoint(desc, EP2_IN)
        _midi_class_ep(desc, 6)
        _audio_endpoint(desc, EP2_OUT)
        _midi_class_ep(desc, 2)

    def num_itfs(self): return 2
    def num_eps(self): return 4

def _jack_in_desc(desc, bJackType, bJackID):
    desc.pack("<BBBBBB", _JACK_IN_DESC_LEN, 0x24, 0x02, bJackType, bJackID, 0x00)

def _jack_out_desc(desc, bJackType, bJackID, bSourceId, bSourcePin):
    desc.pack("<BBBBBBBBB", _JACK_OUT_DESC_LEN, 0x24, 0x03, bJackType, bJackID, 0x01, bSourceId, bSourcePin, 0x00)

def _audio_endpoint(desc, bEndpointAddress):
    desc.pack("<BBBBHBBB", _STD_DESC_AUDIO_ENDPOINT_LEN, 0x05, bEndpointAddress, 2, EP_MIDI_PACKET_SIZE, 0, 0, 0)

def _midi_class_ep(desc, jack_id):
    desc.pack("<BBBBB", _CLASS_DESC_ENDPOINT_LEN, 0x25, 0x01, 1, jack_id)

if __name__ == "__main__":
    import time
    time.sleep_ms(1000)
    midi = MidiTwoCable()
    import usb.device.core
    desc = usb.device.core.Descriptor(bytearray(512))
    # Config header
    desc.pack("<BBHBBBBB", 9, 2, 0, 2, 1, 0, 0x80, 0x32)
    midi.desc_cfg(desc, 0, 1, [])
    wTotalLength = desc.o
    desc.b[2] = wTotalLength & 0xFF
    desc.b[3] = (wTotalLength >> 8) & 0xFF

    print("Config descriptor header:", list(desc.b[:9]))
    print("Descriptor length:", desc.o)
    print("Descriptor hex:", desc.b[:desc.o].hex())
    print("Descriptor bytes:", list(desc.b[:desc.o]))

    dev = usb.device.get()
    dev.init(midi, manufacturer_str="TestMaker", product_str="TestMIDI", serial_str="123456")