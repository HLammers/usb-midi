WORK IN PROGRESS, NOT READY TO USE!

# Introduction

This story starts with the development of [Cybo-Drummer](https://github.com/HLammers/cybo-drummer), a MIDI router/mapper, programmed in MicroPython, with 6 input ports and 6 output ports, specially designed for mapping drum triggers (electronic drum kits&rsquo; modules or brains) to drum computers. Currently it only supports multiple DIN MIDI in and out ports. MicroPython support for user-defined USB devices only became available after I started working on Cybo-Drummer (with the release of MicroPython v1.23, which introduced the [`machine.USBDevice` class](https://docs.micropython.org/en/latest/library/machine.USBDevice.html)), so USB MIDI wasn&rsquo;t part of the initial set-up.

When I started to look into the [USB MIDI module](https://github.com/micropython/micropython-lib/blob/master/micropython/usb/usb-device-midi/usb/device/midi.py) of the [micropython-lib USB Drivers](https://github.com/micropython/micropython-lib/tree/master/micropython/usb) I realized that there was a lot of work to be done still before I could use it to fully integrate USB MIDI support into Cybo-Drummer:
1. Adding support for multiple MIDI ports, so the user could route drum triggers over USB to multiple virtual instruments, and can access the devices connected to the 6 DIN MIDI out ports from the USB connection (a kind of MIDI True from USB to DIN MIDI).
2. Reworking the data transfer flow to process one package at a time, called in a loop, such that it can be integrated with the DIN MIDI data transfer flow and also such that a very dense data stream on one port cannot block other ports&rsquo; data flow.
3. Integrating USB MIDI and DIN MIDI libraries, implementing a two-directional translation step between the 4-bytes Data Packages of USB MIDI 1.0 (or the 4 to 16 bytes Universal MIDI Packages of USB MIDI 2.0) and the byte-stream of DIN MIDI &ndash; which includes challenges like dealing with running status and System Real Time messages (which could &ndash; and should &ndash; be inserted at any point in a byte-stream).
4. Adding support for SysEx, MIDI Clock distribution, MIDI filtering, etc.
5. Making sure the MIDI library works when using multithreading (running it on the second thread, as Cybo-Drumming does).
6. Improving the efficiency of the MIDI library by reducing the number of function calls and using the viper code for time sensitive operations (trying to reduce latency to a minimum).

In this repository I will share my findings, step-by-step developing a fully functional MIDI library supporting both DIN MIDI and USB MIDI. Feel welcome to contact me if you would like to contribute.

This project builds upon:

*For USB MIDI*

* [usb.device/core.py](https://github.com/micropython/micropython-lib/blob/master/micropython/usb/usb-device/usb/device/core.py), Copyright &copy; 2022&ndash;2024 Angus Gratton, published under MIT licence
* [usb.device/midi.py](https://github.com/micropython/micropython-lib/blob/master/micropython/usb/usb-device-midi/usb/device/midi.py), Copyright &copy; 2023 Paul Hamshere, 2023&ndash;2024 Angus Gratton, published under MIT license
* [midi_example.py](https://github.com/micropython/micropython-lib/blob/master/micropython/usb/examples/device/midi_example.py), Copyright &copy; 2023&ndash;2024 Angus Gratton, published under MIT licence

*For DIN MIDI (yet to be integrated)*

* [Simple MIDI Decoder](https://github.com/diyelectromusic/sdemp/blob/main/src/SDEMP/Micropython/SimpleMIDIDecoder.py), Copyright &copy; 2020 diyelectromusic (Kevin), published under MIT licence
* [Cybo-Drummer](https://github.com/HLammers/cybo-drummer), Copyright &copy; 2024&ndash;2025 Harm Lammers, relevant parts published under MIT licence

# Step 1: Testing Multi-Port USB MIDI

## Wish List

* Be able to specify how many MIDI in and out ports will be visible to the host
* Make each port show up with its own name
* Allow the number of MIDI in ports to be different from the number of MIDI out ports

# Comparing Multi-Port USB Midi Approaches

There are two ways to implement multiple MIDI ports over a single USB connection (using the USB MIDI 1.0 protocol):

1. A *multi-interface model* where each port is implemented as a separate MIDI Streaming interface with its own Endpoints and Jacks (see [midi_multi_streaming_example.py](/midi_multi_streaming_example.py))
2. A *single-interface, multiple virtual cables model* which uses only a single MIDI Streaming interface and implements each port as a pair of Jacks and Endpoints within that MIDI Streaming interface (see [midi_multi_cable_example.py](/midi_multi_cable_example.py))

(Theoretically there is a third approach, based on a single MIDI Streaming interface with multiple Endpoints, but this isn&rsquo;t supported by Window, nor by Linux, and probably neither by macOS).

||Windows|Linux|macOS|
|-|-|-|-|
|<b>Multi-port MIDI using virtual Cables</b>|Works|Works|Should work (not tested)|
|<i>&emsp;With port names</i>|Port names ignored (and crashes on single-character names)|Works|Should work (not tested)|
|<i>&emsp;With different IN and OUT names</i>|N/A|Inconsistent results|Not tested|
|<i>&emsp;Asymmetric set-up<br/>&emsp;(different number of IN and OUT ports)</i>|Crashes|Works|Should work (Not tested)|
|<i>&emsp;With Embedded Jacks only</i>|Works|Works|Should work (not tested)|
|<i>&emsp;With Embedded and External Jacks</i>|Works|Works|Should work (not tested)|
|<i>&emsp;With built-in driver</i>|Crashes|Works|Not tested|
|<b>Multi-port MIDI using multiple MIDI Streaming interfaces</b>|Works|Works|Should work (not tested)|
|<i>&emsp;With port names</i>|Port names ignored (and crashes on single-character names)|Works|Should work (not tested)|
|<i>&emsp;With different IN and OUT names</i>|N/A|Doesn&rsquo;t work|Not tested|
|<i>&emsp;Asymmetric set-up<br/>&emsp;(different number of IN and OUT ports)</i>|Crashes|Works|Should work (Not tested)|
|<i>&emsp;With Embedded Jacks only</i>|Works|Works|Should work (not tested)|
|<i>&emsp;With Embedded and External Jacks</i>|Works|Works|Should work (not tested)|
|<i>&emsp;With built-in driver</i>|Crashes|Works|Not tested|

## USB MIDI 2.0

There is one more approach to naming individual ports: switching from USB MIDI 1.0 to USB MIDI 2.0, which replaces virtual Cables with Groups, which can be named. Unfortunately MIDI 2.0 hasn&rsquo;t been adapted widely yet. It requires at least Windows 24H2 (expected to be released in autumn 2025), Linux kernel 6.5+ with alsa-lib version 1.2.10+ or macOS 11+. I have tried setting up a basic MIDI 2.0 configuration (see [midi_multi_2_example.py](/midi_multi_2_example.py)), but so far it didn&rsquo;t work. I can only test it on a Linux device I have available (a Raspberry Pi) and it might be that I need to upgrade alsa-lib to make it support MIDI 2.0 in the first place. I will look into that, but probably I will shelve looking into USB MIDI 2.0 until the next feature update of Windows arrives, so I can use my laptop for testing.

## Conclusion: Use a Multi-Cable Approach

The conclusion is that the multi-interface model is not recommended for cross-platform compatibility and that for naming individual ports should be treated as impossible (unless you&rsquo;re happy sacrifice any compatibility with Windows).

# Next Step

So far I&rsquo;ve demonstrated that multi-port USB MIDI works (be it without naming those ports). My next step is to integrate this with the UART/PIO MIDI drivers as built into [Cybo-Drummer](https://github.com/HLammers/cybo-drummer), because that&rsquo;s what I&rsquo;m doing all this work for.

# Embedded vs external jacks

TO BE CHANGED

Embedded jacks (bJackType=0x01) represent &lsquo;virtual&rsquo; ports inside the device (e.g. software synth); external jacks (bJackType=0x02) represent physical connectors on the device (DIN, TRS, etc.). The USB MIDI specification do not require both &ndash; a device can have only embedded, only external, or both.

The advice to always add both embedded and external jacks comes from a bug in early versions of iOS, which only worked if both were provided. This has long been resolved (apparently since iOS 7, released in 2023), so that is no longer relevant.

To be fully compliant with the USB MIDI specifications it would be best to use external jacks for ports which map to MIDI DIN/TRS connectors and embedded for all other cases, although functionally it doesn&rsquo;t matter which one is used. Eventually I will make it possible to specify embedded/external for each port.