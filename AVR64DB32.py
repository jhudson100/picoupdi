import time
import chips
from typing import Any,Callable
from typing import NamedTuple
from log import debug,error
import typing
from UpdiLink import UpdiLink
from utils import waitUntilTrue
import AVR32DD20

class AVR64DB32(AVR32DD20.AVR32DD20):

    NVMCTRL_CONTROLB = AVR32DD20.AVR32DD20.NVMCTRL_START + 0x01 #everything but flashmap is under ccp
    MAX_NVM_ADDRESS = 0xffff

    def __init__(self) -> None:
        self.mappedSection=-1

    def mapSectionAndGetAddress( self, updiLink:"UpdiLink", addr:int) -> int:
        #addr is a flash address (0...64KB)
        #returns 0x8000-based address for reading
        #and maps the section if needed
        section = addr >> 15
        if self.mappedSection != section:
            self.mapFlashRange(updiLink, section )
        offsetWithinSection = addr & 0x7fff
        a = self.FLASH_START + offsetWithinSection
        assert a >= 0
        assert a <= 0xffff
        return a

    # ~ def getAddressToWrite(self, addr:int) -> int:
        # ~ #addr is a flash address (0...64KB)
        # ~ #returns 0x8000-based address for reading
        # ~ section = addr >> 15
        # ~ assert self.mappedSection == section
        # ~ offsetWithinSection = addr & 0x7fff
        # ~ return self.FLASH_START + offsetWithinSection

    #if section is 0: Map 0...32KB-1 to FLASH_START...FLASH_START+32K
    #if section is 1: Map 32KB...64KB-1 to FLASH_START...FLASH_START+32K
    def mapFlashRange(self,updiLink:"UpdiLink", section:int) -> None:
        debug("mapFlashRange: Section",section)
        assert section >=0 and section < 4
        if self.mappedSection == section:
            debug("Already mapped; no need to change")
            return
        self.mappedSection = section
        v = updiLink.LDS(addr=self.NVMCTRL_CONTROLB)
        v2 = (v & 0b11001111) | (section << 4 )
        debug(f"Changed CONTROLB from {v:08b} to {v2:08b}")
        updiLink.STS(addr=self.NVMCTRL_CONTROLB, data=v2)

    def beginFlashPage(self, updiLink: "UpdiLink", eraseAndWrite: bool, offset:int) -> None :
        debug("AVR64DB32: beginFlashPage for offset",hex(offset))
        #the avr32dd does not have a page buffer, so we need not clear it
        #however, there is no "erase and write" command, so if we want to erase
        #the page, we must do that now as a separate command
        self.writeCommandToNVMController( updiLink, self.NVM_NOCMD )

        #if we're erasing, we have to start on a page boundary.
        #if we're only writing, we can start anywhere
        if eraseAndWrite:
            assert offset % self.PAGE_SIZE == 0
        assert offset >= 0
        assert offset < 65536
        self.mapFlashRange(updiLink, offset >> 15 )
        offset %= 32768
        super().beginFlashPage(updiLink,eraseAndWrite,offset)

    def beginFlashPageNoMap(self, updiLink: "UpdiLink", eraseAndWrite: bool, offset:int) -> None :
        #don't change the memory map and don't reduce offset modulo 32K
        #useful if the caller has already handled those tasks.
        super().beginFlashPage(updiLink,eraseAndWrite,offset)

    def storeToFlash(self,updiLink: "UpdiLink", offset:int, value:int) -> None:
        super().storeToFlash(updiLink,offset%32768,value)

    def setClock(self, updiLink:"UpdiLink", clock: int) -> None:
        D = { 1: 0, 2: 1, 3: 2, 4: 3, 8: 5, 12: 6, 16: 7, 20: 8, 24: 9}
        if clock not in D:
            tmp = sorted(list(D.keys()))
            tmp2 = ", ".join([str(q) for q in tmp])
            error("Invalid clock speed; must be one of", tmp2)
        freq = D[clock]
        CLKBASE = 0x60
        v = updiLink.LDS(addr=CLKBASE + 0x08 )
        v &= 0b11000011
        v |= (freq<<2)
        updiLink.STS(addr=self.CCP_ADDR, data=self.ALLOW_IOREG )
        updiLink.STS(addr=CLKBASE + 0x08, data=v)



chips.registerChip("avr64db28", lambda: AVR64DB32()  )
chips.registerChip("avr64db32", lambda: AVR64DB32()  )
chips.registerChip("avr64db48", lambda: AVR64DB32()  )
chips.registerChip("avr64db64", lambda: AVR64DB32()  )
