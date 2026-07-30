
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
import log

import typing
if typing.TYPE_CHECKING:
    from PicoLink import PicoLink

from utils import waitUntilTrue
from Exceptions import ProtocolError
import chips

SIZE_BYTE = 0b00
SIZE_WORD = 0b01
SIZE_LONG = 0b10        #three bytes

PTR_DEREF = 0b00
PTR_DEREF_POSTINC = 0b01
PTR_ITSELF = 0b10

KEY_SIZE_64_BITS = 0b00
KEY_SIZE_128_BITS = 0b01

SIB_OP_SEND_KEY = 0b0
SIB_OP_RECV_SIB = 0b1

#write these lsb first (i.e., right to left)
KEY_CHIP_ERASE    =      list([int(q) for q in reversed(b"NVMErase" )])
KEY_CHIP_NVM_PROG =      list([int(q) for q in reversed(b"NVMProg " )])
KEY_CHIP_USERROW_WRITE = list([int(q) for q in reversed(b"NVMUS&te" )])

GUARD_TIME_128_CYCLES = 0x0
GUARD_TIME_32_CYCLES = 0x2
GUARD_TIME_16_CYCLES = 0x3
GUARD_TIME_8_CYCLES = 0x4
GUARD_TIME_4_CYCLES = 0x5
GUARD_TIME_2_CYCLES = 0x6
GUARD_TIME_OFF = 0x7




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


def debug(*args:Any) -> None:
    return
    x = [str(q) for q in args]
    print(" ".join(x))


class UpdiLink:
    def __init__(self, picoLink: "PicoLink", chip: chips.Chip):
        self.connected=False
        self.connectCallbacks: list[Callable[[UpdiLink],None]]=[]
        self.disconnectCallbacks: list[Callable[[UpdiLink],None]]=[]
        self.gpio = -1
        self.pioF:int=DEFAULT_PIO_FREQUENCY
        self.picoLink=picoLink
        self.enableProgrammingOnConnect = True
        self.chip=chip

    def setGPIO(self,g:int) -> None:
        self.gpio=g

    def setPioFrequency(self,f:int) -> None:
        self.pioF=f

    def getNVMControllerBusy(self) -> bool:
        return self.chip.getNVMControllerBusy(self)

    def connect(self) -> None:
        if not self.connected:
            if self.gpio == -1:
                raise ProtocolError("GPIO not set")
            self.picoLink.CONNECT_UPDI(
                gpio=self.gpio,
                pioFrequency=self.pioF
            )
            self.connected=True

            if self.enableProgrammingOnConnect:
                self._enableNVMProgramming()

            debug("UpdiLink.connect() completed")

    def setClock(self, speed: int) -> None:
        allowed = {
            4: 3, 8: 2, 16: 1, 32: 0
        }
        if speed not in allowed:
            tmp = sorted(list(allowed.keys()))
            tmp2 = ", ".join([str(q) for q in tmp])
            log.error("Bad UPDI clock speed; must be one of",tmp2)

        xx = self.LDCS(addr=0x09)

        xx &= 0b11111100
        xx |= allowed[speed]
        self.STCS(addr=0x09, data=xx)

    def requestReset(self) -> None:
        debug("requestReset")
        self.connect()
        self.STCS(addr=0x08, data=0x59)  #request reset: Set RSTREQ bit
        time.sleep(0.001)  #might not be needed
        self.STCS(addr=0x08, data=0x00)  #clear reset request

    def enableNVMProgramming(self) -> None:
        self.connect()
        self._enableNVMProgramming()

    def _enableNVMProgramming(self) -> None:
        ks = self.LDCS(0x07)
        if ks & (1<<4):
            return

        debug("send key: NVM_PROG")
        self.KEY(SIB_OP_SEND_KEY,KEY_SIZE_64_BITS,KEY_CHIP_NVM_PROG)

        ks = self.LDCS(0x07)
        if not (ks & (1<<4)):
            #if we didn't get nvm prog enabled,
            #reset the chip and then send the key again
            debug("Programming was not enabled")
            debug("Send break")
            self.picoLink.BREAK()
            debug("reset chip")
            self.requestReset()
            debug("send key: NVM_PROG")
            self.KEY(SIB_OP_SEND_KEY,KEY_SIZE_64_BITS,KEY_CHIP_NVM_PROG)

        ks = self.LDCS(0x07)
        if not (ks & (1<<4)):
            raise ProtocolError("Chip will not enter programming mode")

        debug("Chip is in programming mode")

        debug("Resetting chip")
        self.requestReset()

        time.sleep(0.001)

        #read ASI system status register and check PROGSTART bit (bit 3)
        for i in range(10):
            debug("Check PROGSTART bit")
            v = self.LDCS(0x0b)
            progstart = v & (1<<3)
            debug("ASI system status=",bin(v))
            if progstart:
                break
            else:
                time.sleep(0.005)
        else:
            debug("PROGSTART never became 1")
            raise TimeoutError()

        debug("//////////////Programming enabled")
        #if we get here, we've enabled programming
        return

    def disableUpdiAndDisconnect(self) -> None:
        """Disable UPDI on the AVR"""
        if not self.connected:
            raise ProtocolError("UPDI link not established")
        #set updiDisable bit in control register B
        self.STCS(addr=0x03, data=(1<<2) )
        self.disconnect()

    def disconnect(self) -> None:
        """Close UPDI link"""
        if self.connected:
            self.picoLink.DISCONNECT_UPDI()
            self.connected=False
            for c in self.disconnectCallbacks:
                c(self)

    def addConnectCallback(self,c: Callable[["UpdiLink"],None]) -> None:
        self.connectCallbacks.append(c)

    def addDisconnectCallback(self,c:Callable[["UpdiLink"],None]) -> None:
        self.disconnectCallbacks.append(c)

    def sendByte(self,b:int,respSize:int=0) -> list[int]:
        self.connect()
        data = self.picoLink.DATA(toSend=[b],numExpected=respSize)
        return data


    def send(self,toSend: list[int], respSize:int) -> list[int]:
        self.connect()
        debug(f"send:",[hex(q) for q in toSend] , "expect",respSize)
        data = self.picoLink.DATA(toSend=toSend,numExpected=respSize)
        return data

    def recvByte(self) -> int:
        self.connect()
        data = self.picoLink.DATA(toSend=[],numExpected=1)
        return data[0]

    def recvBytes(self, count:int) -> list[int]:
        self.connect()
        data = self.picoLink.DATA(toSend=[],numExpected=count)
        return data

    def sendAddr(self,addr:int) -> None:
        """Send an address. Little endian order."""
        self.sendByte (addr & 0xff )
        self.sendByte( (addr>>8) & 0xff )

    def LDS(self,addr:int) -> int:
        """Load (read) one byte of data using immediate address"""
        debug("LDS")
        v = self.send(
            [
                0x55,
                0b0000_0000 | (SIZE_WORD << 2) | SIZE_BYTE,
                addr & 0xff,
                (addr>>8)&0xff
            ],
            1
        )
        return v[0]

    def STS(self,addr:int,data:int) -> None:
        """Store (write) one byte of data using immediate address"""
        # ~ debug("STS: addr=",hex(addr),"data=",data,"=",hex(data) )
        self.send(
            [
                0x55,
                0b0100_0000 | (SIZE_WORD << 2) | SIZE_BYTE,
                addr & 0xff,
                (addr>>8)&0xff
            ], 0
        )
        self.waitAck()
        self.sendByte(data)
        self.waitAck()



    def waitAck(self) -> None:
        debug("waitAck")
        resp = self.picoLink.DATA(toSend=[], numExpected=1)
        if resp[0] != 0x40:
            raise ProtocolError(f"Expected ACK (0x40) got 0x{resp[0]:02x}")
        debug("Got ACK (0x40)")

    def LD(self,addr:int,count:int) -> list[int]:
        """load (read) multiple bytes of data"""
        if count == 0:
            return []
        self.send(
            [
                0x55,
                (0b0110_0000 | (PTR_ITSELF<<2) | SIZE_WORD),
                addr & 0xff,
                (addr>>8)&0xff
            ], 0
        )
        self.waitAck()
        data=[]
        for i in range(count):
            b = self.send(
                [
                    0x55,
                    (0b0010_0000 | (PTR_DEREF_POSTINC<<2) | SIZE_BYTE )
                ],
                1
            )
            data.append(b[0])
        return data

    def ST(self,ptrType:int, dataSize:int, data:int) -> None:
        debug("ST: ptrType=",ptrType,"size=",dataSize,"value=",data,"=",hex(data))
        #dataSize is one of the SIZE_xxx constants, not the size in bytes

        lst = [
            0x55,
            (0b0110_0000 | (ptrType<<2) | (dataSize) )
        ]
        #dataSize is 00 (for one byte), 01 (for two bytes), or 10 (for three bytes)
        #Thus, we add one for the loop count
        for i in range(dataSize+1):
            lst.append( data & 0xff )
            data >>= 8

        self.send( lst, 0 )
        self.waitAck()

    def LDCS(self,addr:int) -> int:
        """load (read) control/status register"""
        debug("LDCS",hex(addr))
        d = self.send( [ 0x55, ( 0b1000_0000 | addr ) ], 1 )
        return d[0]

    def STCS(self,addr:int,data:int) -> None:
        """store (write) control/status register"""
        debug("STCS",hex(addr),hex(data))
        self.send( [0x55, ( 0b1100_0000 | addr ), data ] , 0 )

    def KEY(self,sibOp:int, keySize:int, additionalData:list[int],respSize:int=0) -> list[int]:
        """Low level key/sib command"""
        debug("KEY: op=",sibOp,"keySize=",keySize)
        return self.send( [0x55, (0b1110_0000 | (sibOp << 2) | keySize ) ] +
            additionalData, respSize )
