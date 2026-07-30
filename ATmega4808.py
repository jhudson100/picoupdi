#this also works with ATmega4809

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
from log import debug,error
import typing
from UpdiLink import UpdiLink
from utils import waitUntilTrue
import chips

class ATmega4808(chips.Chip):
    NAME="ATmega 4808/4809"
    NVM_NOCMD = 0x00
    NVM_CHER = 0x05 #chip erase

    #ref: page 64 (ch 6.1, Peripheral Module Address Map)
    #ref: ch 7 (memories) tells start for eeprom, etc.
    CPU_START = (0x0030)
    NVMCTRL_START = (0x1000)
    FLASH_START = (0x4000)
    EEPROM_START = (0x1400)
    SIGROW_START = (0x1100)
    FUSE_START = 0x1280
    FLASH_PAGE_SIZE=128

    def __init__(self) -> None:
        pass

    def getFlashStart(self) -> int:
        return self.FLASH_START

    def getFlashPageSize(self) -> int:
        return self.FLASH_PAGE_SIZE


    def mapSectionAndGetAddress( self, updiLink:"UpdiLink", addr:int) -> int:
        assert 0, "FIXME: Check: is this correct?"
        #addr = flash-based address (0...32KB)
        #returns address of mapped flash data (0x8000 based)
        return self.FLASH_START + addr

    def writeCommandToNVMController(self,updiLink: "UpdiLink", cmd: int) -> None:
        debug("writeCommandToNVMController:",hex(cmd))

        #write key to configuration change protection register (CPU_START + offset 0x04)
        #page 40: Key for I/O registers is 0xd8. key for SPM is 0x9d.
        #pg 85: CTRLA is under SPM; CTRLB and C are IOREG
        updiLink.STS(addr=self.CPU_START + 0x04, data=0x9d )

        #write 'no-cmd' to ensure we're in a valid state for
        #issuing a new command
        updiLink.STS(addr=self.NVMCTRL_START+0x00, data=cmd)

        self.checkNVMError(updiLink)

    def eraseFlash(self,updiLink: "UpdiLink") -> None:
        self.writeCommandToNVMController( updiLink, self.NVM_NOCMD )
        self.writeCommandToNVMController( updiLink, self.NVM_CHER)
        waitUntilTrue( lambda: False == updiLink.getNVMControllerBusy() )
        self.writeCommandToNVMController( updiLink, self.NVM_NOCMD )
        waitUntilTrue(lambda: False == updiLink.getNVMControllerBusy() )

    def getNVMAddr(self, updiLink: "UpdiLink") -> int:
        lo = updiLink.LDS(addr=self.NVMCTRL_START + 0x08)
        hi = updiLink.LDS(addr=self.NVMCTRL_START + 0x09)
        return (hi<<8)|lo

    def getNVMControllerBusy(self, updiLink: "UpdiLink") -> bool:
        reg = updiLink.LDS(self.NVMCTRL_START + 0x02)
        if reg & 0b100:
            error(f"NVM error: {reg:03b}")
        return (reg & 0b11) != 0

    def checkNVMError(self, updiLink: "UpdiLink") -> None:
        reg = updiLink.LDS(self.NVMCTRL_START + 0x02)
        if reg & 0b100:
            error("NVM error")

    def beginFlashWrite(self,updiLink: "UpdiLink") -> None:
        #ensure no ongoing write operations
        debug("beginFlashWrite")
        waitUntilTrue(lambda: False == updiLink.getNVMControllerBusy() )
        self.writeCommandToNVMController( updiLink, self.NVM_NOCMD )
        self.checkNVMError(updiLink)

    def clearPageBuffer(self,updiLink:"UpdiLink") -> None:
        debug("CLEAR PAGE BUFFER")
        self.checkNVMError(updiLink)
        self.writeCommandToNVMController( updiLink, 0x04 )      #page buffer clear
        #takes 7 CPU clock cycles. If we assume
        #1MHz CPU, this is 7us
        #time.sleep(0.01)
        self.checkNVMError(updiLink)
        self.writeCommandToNVMController( updiLink, self.NVM_NOCMD )
        #unlike the AVRDU16, we need not issue a separate "begin flash write" command
        #to the NVM controller

    def beginFlashPage(self, updiLink: "UpdiLink", eraseAndWrite: bool, offset:int) -> None :
        debug("beginFlashPage")
        self.clearPageBuffer(updiLink)
        #we don't use eraseAndWrite here; that decision is made
        #when the page has been ended

    def endFlashPage(self, updiLink: "UpdiLink", eraseAndWrite:bool) -> None:
        debug("endFlashPage: addr=",hex(self.getNVMAddr(updiLink)))

        if eraseAndWrite:
            #erase and write page
            self.writeCommandToNVMController(updiLink,0x03)
        else:
            #just write page; no erase
            self.writeCommandToNVMController(updiLink,0x01)

        #2ms for write, 4ms for erase+write
        time.sleep(0.004)
        waitUntilTrue(lambda: False == updiLink.getNVMControllerBusy() )

    def storeToFlash(self,updiLink: "UpdiLink", offset:int, value:int) -> None:
        #write value to store in flash
        debug("store to flash at",hex(offset))
        updiLink.STS(self.FLASH_START+offset,value)

    def finishFlashWrite(self,updiLink: "UpdiLink") -> None:
        debug("finishFlashWrite")
        waitUntilTrue(lambda: False == updiLink.getNVMControllerBusy() )
        #write nocmd to complete operation
        self.writeCommandToNVMController(updiLink,self.NVM_NOCMD)
        #this probably isn't needed
        waitUntilTrue(lambda: not updiLink.getNVMControllerBusy() )

chips.registerChip("atmega4808", lambda: ATmega4808() )
chips.registerChip("atmega4809", lambda: ATmega4808() )
