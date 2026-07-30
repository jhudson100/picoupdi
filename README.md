# picoupdi
A tool that programs UPDI-compatible AVR microcontrollers using a Raspberry Pi Pico.


Setup
======

1. Install pyserial on your computer, if it isn't already installed.

2. Install Micropython on the Pico and copy picoupdibackend.py to the
Pico using a tool like Thonny, ampy, mpremote, or rshell.

3. The AVR will need power (3.3V) and ground connections.
Also connect the UPDI pin on the AVR to one of the Pico's
GPIO pins. (Example: Pin 21 on the lower right corner
of the Pico is GP16). The GP number will need to be supplied
to picoupdi.

4. Connect the Pico to your computer and run picoupdi.py.


Example
========

    python picoupdi.py --speed 115200 --gpio 16 --chip avr32db32 write file_to_write_to_avr

Replace the value after "--chip" with the chip model that you have.

There are a number of command line options available; use the
--help argument to get a description of them.
