
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
from Exceptions import Timeout,ProtocolError,CommandException
import typing
if typing.TYPE_CHECKING:
    from SerialPort import SerialPort
    from ATmega4808 import ATmega4808
from UpdiLink import UpdiLink
from utils import waitUntilTrue
import chips

#freq of 16000   = 1000 bits per second
#freq of 32000   = 2k bits per second
#freq of 64000   = 4k bits per second
#freq of 128_000 = 8k bits per second
#freq of 256_000 = 16kbits per second (about 67us per bit as measured on scope = 15kbit)
#freq of 512_000 = 32kbits per second
#freq of 1_024_000 = 64kbits per second. Acceptable ringing.
#freq of 2_048_000 = 128kbits per second. Some ringing.
#freq of 4_096_000 = 256kbits per second (a bit over max recommended with 4MHz oscillator)
#                    works, but there's a lot of ringing on the signals. 4us per bit
DEFAULT_PIO_FREQUENCY=256000


VERSION=0x20260508



def debug(*args:Any) -> None:
    return
    x = [str(q) for q in args]
    print(" ".join(x))


class PicoLink:

    def __init__(self, port: "SerialPort",chip: chips.Chip):
        self.connected=False
        self.connectCallbacks:list[Callable[["PicoLink"],None]]=[]
        self.disconnectCallbacks:list[Callable[["PicoLink"],None]]=[]
        self.updiLink = UpdiLink(self,chip)
        self.port:SerialPort=port

    def addConnectCallback(self,c:Callable[["PicoLink"],None]) -> None:
        self.connectCallbacks.append(c)

    def addDisconnectCallback(self,c:Callable[["PicoLink"],None]) -> None:
        self.disconnectCallbacks.append(c)

    def disconnect(self) -> None:
        if self.connected:
            self.updiLink.disconnect()
            self.connected=False
            for c in self.disconnectCallbacks:
                c(self)

    def connect(self) -> None:
        #if we're already connected, do nothing
        if self.connected:
            return

        #cancel any running program and/or its input with ctrl-c
        for i in range(3):
            self.port.write(b"\x03")
        #switch to repl mode
        self.port.write(b"\x02")
        #soft reset to ensure it's in repl mode
        self.port.write(b"\x04")
        self.port.write(b"import picoupdibackend\r\n")
        self.waitForString("\r\nPICO UPDI BACKEND\r\n")
        self.waitForPrompt()
        self.connected=True

        bversion = self.GET_VERSION()
        if bversion != VERSION:
            self.connected=False
            raise ProtocolError(f"Backend version mismatch: Expected {VERSION:08x} but got {bversion:08x}")

        for c in self.connectCallbacks:
            c(self)

    def GET_VERSION(self) -> int:
        r = self._sendCommand("GET_VERSION")
        return r["version"] #type:ignore

    def SET_TIMEOUT(self,timeout:int) -> None:
        self._sendCommand("SET_TIMEOUT",timeout=timeout)

    def CONNECT_UPDI(self,gpio:int,pioFrequency:int=0) -> None:
        self._sendCommand("CONNECT_UPDI",gpio=gpio,pioFrequency=pioFrequency)

    def DISCONNECT_UPDI(self) -> None:
        self._sendCommand("DISCONNECT_UPDI")

    def BREAK(self) -> None:
        self._sendCommand("BREAK")

    def STATS(self) -> tuple[int,int]:
        r = self._sendCommand("STATS")
        return ( r["sent"], r["received"] )

    def DATA(self,toSend:list[int],numExpected:int) -> list[int]:
        # ~ debug("send to avr:",[hex(q) for q in toSend],"expect",numExpected,"bytes back")
        r = self._sendCommand("DATA",toSend=toSend,numExpected=numExpected)
        return r["data"] #type:ignore

    def SEND_BLOCK(self,data:list[int], parity:list[int]) -> None :
        # ~ debug("SEND_BLOCK")
        assert len(parity) == len(data)
        for i in range(0,len(data),256):
            #byte send
            self._sendCommand("SEND_BLOCK",data=data[i:i+256],parity=parity[i:i+256])

    def RECV_BLOCK(self,count:int) -> list[int]:
        # ~ debug("RECV_BLOCK")
        if False: #count % 2 == 0:
            debug("RECV_BLOCK_WORD",count)
            d = self._sendCommand("RECV_BLOCK_WORD",count=count)
        else:
            debug("RECV_BLOCK_BYTE",count)
            d = self._sendCommand("RECV_BLOCK_BYTE",count=count)
        return d["data"] #type:ignore

    def _sendCommand(self, cmd: str, **args:Any) -> dict["str",Any]:
        if not self.connected:
            raise ProtocolError("Not connected to Pico")

        debug("sendCommand:",cmd)
        #Internal function: Don't check if we're connected
        D={"action":cmd}
        for k in args:
            D[k]=args[k]
        J=json.dumps(D)
        self.port.write(J)
        self.port.write(b"\r\n")
        self.port.flush()
        #pico will echo back the data we just sent
        self.waitForString("\n")
        # ~ debug("_sendCommand: Waiting for prompt")
        resp = self.waitForPrompt()
        resp=resp.strip()
        # ~ debug(f"_sendCommand: GOT RESPONSE: -->{resp}<--")
        try:
            R = json.loads(resp)
        except json.decoder.JSONDecodeError as e:
            print("RESP: ->"+resp+"<-")
            raise
        if not R["ok"]:
            raise CommandException("Error",**R)
        return R #type:ignore

    def getBackendVersion(self) -> int:
        r = self._sendCommand("GET_VERSION")
        return r["version"] #type:ignore

    def setTimeout(self,timeout:int) -> None:
        self._sendCommand("SET_TIMEOUT",value=timeout)

    def waitForPrompt(self) -> str:
        PROMPT="@@>"
        return self.waitForString(PROMPT)[:-len(PROMPT)]

    def waitForString(self,needle:str) -> str:
        #debug("WAIT FOR STRING:",repr(needle))
        try:
            haystack=""
            while True:
                c = self.port.read(1).decode(errors="ignore")
                haystack += c
                if haystack.endswith(">~!~"):
                    i = haystack.rfind("~!~<")
                    if i != -1:
                        dbginfo = haystack[i+4:-4]
                        # ~ debug("REMOTE DEBUG INFO:",dbginfo)
                        haystack = haystack[:i]
                if haystack.endswith(needle):
                    # ~ debug("WAIT FOR STRING found {needle} and returns:",haystack)
                    return haystack
        except Exception as e:
            # ~ debug(f"waitForString: waiting for {repr(needle)}:  Got exception {e}")
            # ~ debug("Before exception: Received:",haystack)
            raise
