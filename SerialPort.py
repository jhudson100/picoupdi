
import serial                       #type: ignore
import serial.tools.list_ports      #type: ignore
import argparse
import sys
import time
import json
import struct
import io
from typing import Any,Callable
from typing import NamedTuple
from log import debug
import typing
if typing.TYPE_CHECKING:
    from ATmega4808 import ATmega4808
from PicoLink import PicoLink
from Exceptions import ProtocolError,Timeout
from chips import Chip

TIMEOUT = 5.0

class SerialPort:
    def __init__(self,chip:Chip) -> None:
        self.portname:str|None = None
        self.port: Any|None = None
        self.picoLink = PicoLink(self,chip)

    def setPortName(self,name:str) -> None:
        """Set serial port name. It is an error
           if we're currently connected."""
        if self.picoLink.connected:
            raise ProtocolError("Cannot set port name while connected to Pico")
        self.portname = name

    def connect(self) -> None:
        if self.port != None:
            return

        if not self.portname:
            self.portname = SerialPort.scanForPort()

        if not self.portname:
            raise ProtocolError("No serial port specified")

        self.port = serial.Serial(self.portname,115200,timeout=TIMEOUT)
        self.port.reset_input_buffer()
        self.port.reset_output_buffer()

    def flush(self) -> None:
        if self.port:
            self.port.flush()

    def disconnect(self) -> None:
        if self.port:
            self.picoLink.disconnect()
            self.port.close()
            self.port=None

    def write(self, data:str|bytes|bytearray) -> None:
        if not self.port:
            raise ProtocolError("Not connected")
        if type(data) == str:
            data = data.encode()
        self.port.write(data)
        self.port.flush()

    def read(self,count:int) -> bytes:
        if not self.port:
            raise ProtocolError("Not connected")
        b=bytes()
        for i in range(count):
            r = self.port.read(1)
            if len(r) == 0:
                raise Timeout("Timeout when reading from serial port")
            b += r
        return b

    def close(self) -> None:
        if not self.port:
            return
        self.disconnect()
        self.port.close()
        self.port = None


    @staticmethod
    def listPorts() -> None:
        ports = serial.tools.list_ports.comports()
        for port in ports:
            print(port.device, f"{port.manufacturer}" if port.manufacturer else "")

    @staticmethod
    def scanForPort() -> None|str:
        ports = serial.tools.list_ports.comports()
        for port in ports:
            if str(port.manufacturer).lower() == "micropython" or \
               str(port.hwid).lower() == "2e8a:0005" or \
               "usb serial" in str(port).lower():
                   return port.device   #type:ignore
        return None
