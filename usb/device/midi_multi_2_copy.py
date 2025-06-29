# Requires at least Windows 24H2 (expected to be released in autumn 2025), Linux kernel 6.5 or macOS 11

from micropython import schedule
from usb.device.core import Interface, Buffer

# USB MIDI 2.0 constants
_INTERFACE_CLASS_AUDIO = const(0x01)
_INTERFACE_SUBCLASS_AUDIO_CONTROL = const(0x01)
_JACK_TYPE_BLOCK = const(0x10)  # MIDI 2.0 Group Terminal Block
_EP_IN_FLAG = const(0x80)
_EP_PACKET_SIZE = 64

class MidiMulti(Interface):
    """
    USB MIDI 2.0 device: single interface, single IN/OUT endpoint, multiple groups (ports).
    """
    def __init__(self, num_ports=1, port_names=None):
        super().__init__()
        self.num_ports = num_ports
        if port_names and len(port_names) == num_ports:
            self.port_names = port_names
        else:
            self.port_names = [f"MIDI Group {i+1}" for i in range(num_ports)]
        self.ep_out = None  # RX endpoint (host to device)
        self.ep_in = None   # TX endpoint (device to host)
        self._rx_buffer = Buffer(_EP_PACKET_SIZE)
        self._tx_buffer = Buffer(_EP_PACKET_SIZE)
        self._in_callbacks = [None] * num_ports

    def set_in_callback(self, group, callback):
        if 0 <= group < self.num_ports:
            self._in_callbacks[group] = callback

    def send_ump(self, group, ump_bytes):
        """Send a UMP (Universal MIDI Packet) to the host on the given group."""
        # UMP is 4/8/12/16 bytes, send as-is, group is 0-15
        # Place group in lower 4 bits of first word if needed (for MIDI 2.0 spec compliance)
        _buffer = self._tx_buffer
        w = _buffer.pend_write()
        n = len(ump_bytes)
        if len(w) < n:
            return False
        w[:n] = ump_bytes
        _buffer.finish_write(n)
        self._tx_xfer()
        return True

    def desc_cfg(self, desc, itf_num, ep_num, strs):
        # AudioControl interface
        desc.interface(itf_num, 0, 1, 1)
        desc.pack('<BBBHHBB', 9, 0x24, 0x01, 0x0200, 9, 1, itf_num + 1)

        # MIDIStreaming interface
        ms_if_num = itf_num + 1
        desc.interface(ms_if_num, 2, 1, 3)

        # Class-specific MS header
        cs_len = 7 + self.num_ports * 12
        desc.pack('<BBBHH', 7, 0x24, 0x01, 0x0200, cs_len)

        group_block_ids = []
        for i, name in enumerate(self.port_names):
            iBlockItem = len(strs)
            strs.append(name)
            block_id = i+1
            group_block_ids.append(block_id)
            desc.pack('<BBBBBBBBBBBB',
                12,    # bLength
                0x24,  # CS_INTERFACE
                0x0A,  # GRP_TERM_BLOCK
                0x01,  # Embedded
                block_id,   # bJackID (unique)
                0,      # bNrInputPins
                0,      # baSourceID
                0,      # baSourcePin
                iBlockItem,    # iJack
                block_id,   # bGroupTerminalBlockID
                i,      # bGroupID (UMP group 0..15)
                iBlockItem     # iBlockName (port name)
            )

        # OUT endpoint (host->device)
        self.ep_out = ep_num
        desc.pack('<BBBBHB', 7, 0x05, ep_num, 3, 64, 1)
        desc.pack('<BBBBB' + 'B'*self.num_ports, 5 + self.num_ports, 0x25, 0x01, self.num_ports, *group_block_ids)
        # IN endpoint (device->host)
        self.ep_in = ep_num | 0x80
        desc.pack('<BBBBHB', 7, 0x05, self.ep_in, 3, 64, 1)
        desc.pack('<BBBBB' + 'B'*self.num_ports, 5 + self.num_ports, 0x25, 0x01, self.num_ports, *group_block_ids)

    def num_itfs(self):
        return 2

    def num_eps(self):
        return 2

    def on_open(self):
        super().on_open()
        self._tx_xfer()
        self._rx_xfer()

    def _tx_xfer(self):
        buf = self._tx_buffer
        if self.is_open() and not self.xfer_pending(self.ep_in) and buf.readable():
            self.submit_xfer(self.ep_in, buf.pend_read(), self._tx_cb)

    def _tx_cb(self, ep, res, num_bytes):
        if res == 0:
            self._tx_buffer.finish_read(num_bytes)
        self._tx_xfer()

    def _rx_xfer(self):
        buf = self._rx_buffer
        if self.is_open() and not self.xfer_pending(self.ep_out) and buf.writable():
            self.submit_xfer(self.ep_out, buf.pend_write(), self._rx_cb)

    def _rx_cb(self, ep, res, num_bytes):
        if res == 0:
            self._rx_buffer.finish_write(num_bytes)
            schedule(self._on_rx, None)
        self._rx_xfer()

    def _on_rx(self, _):
        # UMPs are 4*n bytes, for n=1..4
        _buffer = self._rx_buffer
        m = _buffer.pend_read()
        i = 0
        while i <= len(m) - 4:
            # Parse 4 bytes for group/packet type
            group = m[i] & 0x0F
            ump_len = 4 * ((m[i] >> 4) + 1)  # UMP length: (MsgType + 1) * 4 bytes
            if ump_len > len(m) - i:
                break  # incomplete packet
            cb = self._in_callbacks[group]
            if cb:
                cb(m[i:i+ump_len])
            i += ump_len
        _buffer.finish_read(i)