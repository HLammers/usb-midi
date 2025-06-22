WORK IN PROGRESS, NOT READY TO USE!

# Introduction

This repository the results of my search for a multi-port USB MIDI implementation for MicroPython. I&rsquo;ve taken the [micropython-lib USB Drivers](https://github.com/micropython/micropython-lib/tree/master/micropython/usb) as starting point, and it still requires [core.py](https://github.com/micropython/micropython-lib/blob/master/micropython/usb/usb-device/usb/device/core.py) from the usb.device library, included here in the [usb/device/ folder](usb/device/) (Copyright &copy; 2022-2024 Angus Gratton, published under MIT license), but I ended up extensively rewriting the [USB MIDI module](https://github.com/micropython/micropython-lib/blob/master/micropython/usb/usb-device-midi/usb/device/midi.py) (Copyright &copy; 2023 Paul Hamshere, 2023-2024 Angus Gratton, published under MIT license), to try to find a way to meet the following requirements:

* Be able to specify how many MIDI input and MIDI output ports will be visible to the host
* Allow the number of MIDI input ports to be different from the number of MIDI output ports
* Make each port show up with its own name *(spoiler: as I will explain below, this failed)*

I did a thorough test of different ways to implement the above and what follows is a summary of what I learned.

# Multi-Port USB Midi Approaches

There are two ways to implement multiple MIDI ports over a single USB connection:

1. A *multi-interface model* where each port is implemented as a separate MIDIStreaming interface with its own endpoints and jacks
2. A *single-interface, multiple virtual cables model* which uses only a single MIDIStreaming interface and implements each port as a pair of Jacks and endpoints within that MIDIStreaming interface

The key difference between those two approaches is that in the multi-interface model each port, being a streaming interface, has its own descriptor, which &ndash; theoretically &ndash; allows each port to be named differently. If I try this, I can get it to work on Linux (and it should work on a Mac as well, but don&rsquo;t have one, so I didn&rsquo;t test), but not on Windows. This turns out to be a huge limitation of Windows&rsquo; default &lsquo;class-compliant&rsquo; USB MIDI 1.0 driver: first of all it requires the USB descriptor to be composed very carefully, in a very specific order, but as soon as I add interface name strings to the interface descriptor (referenced by [iInterface](https://www.beyondlogic.org/usbnutshell/usb5.shtml)) it all falls apart and there is now way to get it to work.

The conclusion is that the multi-interface model is not recommended for cross-platform compatibility and that for naming individual ports should be treated as impossible (unless you&rsquo;re happy sacrifice any compatibility with Windows).

# USB MIDI 2.0

There is one more approach to naming individual ports: switching from USB MIDI 1.0 to USB MIDI 2.0, which allows naming virtual cables. The disadvantage is that only very recently MIDI 2.0 support has been added to all mayor OSes, so people running older systems might not be able to use it. I&rsquo;m also not sure if it has already been implemented into the version of TinyUSB used by MicroPython.

# Next Step

So far I&rsquo;ve demonstrated that multi-port USB MIDI works (be it without naming those ports). My next step is to integrate this with the UART/PIO MIDI drivers as built into [Cybo-Drummer](https://github.com/HLammers/cybo-drummer), because that&rsquo;s what I&rsquo;m doing all this work for.

# Embedded vs external jacks

Embedded jacks (bJackType=0x01) represent &lsquo;virtual&rsquo; ports inside the device (e.g. software synth); external jacks (bJackType=0x02) represent physical connectors on the device (DIN, TRS, etc.). The USB MIDI specification do not require both &ndash; a device can have only embedded, only external, or both.

The advice to always add both embedded and external jacks comes from a bug in early versions of iOS, which only worked if both were provided. This has long been resolved (apparently since iOS 7, released in 2023), so that is no longer relevant.

To be fully compliant with the USB MIDI specifications it would be best to use external jacks for ports which map to MIDI DIN/TRS connectors and embedded for all other cases, although functionally it doesn&rsquo;t matter which one is used. Eventually I will make it possible to specify embedded/external for each port.