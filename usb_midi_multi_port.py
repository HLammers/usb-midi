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
EP_MIDI_PACKET_SIZE = 32

# Endpoint address helpers for IN/OUT pairs, up to 8 ports:
def midi_ep_out(n): return 0x01 + n  # OUT  endpoints: 0x01, 0x02, 0x03, ...
def midi_ep_in(n):  return 0x81 + n  # IN   endpoints: 0x81, 0x82, 0x83, ...

class MidiMultiCable(Interface):
    def __init__(self, num_ports=2, ac_interface_name="MIDI Control"):
        self.num_ports = num_ports
        self.ac_interface_name = ac_interface_name

    def desc_cfg(self, desc, itf_num, ep_num, strs):
        ac_if_str_idx = self._get_str_idx(strs, self.ac_interface_name)
        # AudioControl interface (interface 0)
        desc.interface(itf_num, 0, _INTERFACE_CLASS_AUDIO, _INTERFACE_SUBCLASS_AUDIO_CONTROL, ac_if_str_idx)
        desc.pack("<BBBHHBB", 9, 0x24, 0x01, 0x0100, 0x0009, 1, itf_num + 1)

        # MIDIStreaming Interface (interface 1)
        ms_if_num = itf_num + 1
        desc.interface(ms_if_num, 2 * self.num_ports, _INTERFACE_CLASS_AUDIO, _INTERFACE_SUBCLASS_AUDIO_MIDISTREAMING)
        # Class-specific MS Interface header
        total_class_len = (
            7 + 2 * self.num_ports * _JACK_IN_DESC_LEN + 2 * self.num_ports * _JACK_OUT_DESC_LEN
        )
        desc.pack("<BBBHH", 7, 0x24, 0x01, 0x0100, total_class_len)

        # Jacks: for each port: Embedded IN, External IN, Embedded OUT, External OUT
        for i in range(self.num_ports):
            emb_in_id  = 1 + i
            ext_in_id  = 1 + self.num_ports + i
            emb_out_id = 1 + 2 * self.num_ports + i
            ext_out_id = 1 + 3 * self.num_ports + i
            _jack_in_desc(desc, _JACK_TYPE_EMBEDDED, emb_in_id)
            _jack_in_desc(desc, _JACK_TYPE_EXTERNAL, ext_in_id)
            _jack_out_desc(desc, _JACK_TYPE_EMBEDDED, emb_out_id, ext_in_id, 1)
            _jack_out_desc(desc, _JACK_TYPE_EXTERNAL, ext_out_id, emb_in_id, 1)

        # Endpoints for all ports
        for i in range(self.num_ports):
            emb_in_id  = 1 + i
            emb_out_id = 1 + 2 * self.num_ports + i
            _audio_endpoint(desc, midi_ep_in(i))
            _midi_class_ep(desc, emb_out_id)
            _audio_endpoint(desc, midi_ep_out(i))
            _midi_class_ep(desc, emb_in_id)

    def num_itfs(self): return 2  # AudioControl + MIDIStreaming
    def num_eps(self): return 2 * self.num_ports

    def _get_str_idx(self, strs, s):
        if not s:
            return 0
        if s in strs:
            return strs.index(s) + 1
        strs.append(s)
        return len(strs)

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
    NUM_PORTS = 3  # Set to any value you want (up to USB endpoint limit, e.g. 8)
    midi = MidiMultiCable(num_ports=NUM_PORTS)
    import usb.device.core
    desc = usb.device.core.Descriptor(bytearray(512))
    # Config header: wTotalLength will be filled later
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