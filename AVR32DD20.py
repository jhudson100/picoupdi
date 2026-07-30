import time
import chips
from typing import Any,Callable
from typing import NamedTuple
from log import debug,debugRep,error
import typing
from UpdiLink import UpdiLink
from utils import waitUntilTrue

class AVR32DD20(chips.Chip):
    NVM_NOCMD = 0x00
    NVM_FLASH_WRITE = 0x02
    NVM_PAGE_ERASE = 0x08
    NVM_CHIP_ERASE = 0x20 #chip erase
    MAX_NVM_ADDRESS = 0xffff   #max address expected in NVMCTRL STATUS register

    PAGE_SIZE=512
    #ref: Peripheral Module Address Map tells address of peripherals
    CPU_START = 0x0030
    NVMCTRL_START = 0x1000

    #ref: Memories chapter tells start for eeprom, etc.
    FLASH_START = 0x8000        #up to 32KB flash
    EEPROM_START = 0x1400
    SIGROW_START = 0x1100
    FUSE_START = 0x1050
    FLASH_PAGE_SIZE=512

    #other values
    CCP_ADDR = CPU_START + 0x04      #location to write for config change protection
    ALLOW_SPM = 0x9d                 #allow storing to flash (store prog mem)
    ALLOW_IOREG = 0xd8              #allow changes to IO registers

    NVMCTRL_CONTROLA = NVMCTRL_START + 0x00

    NVMCTRL_ADDRESS = NVMCTRL_START + 0x08
    NVMCTRL_ADDRESS_COUNT = 3       #number of bytes in nvm_address

    NVMCTRL_STATUS = NVMCTRL_START + 0x02       #status: busy bit
    NVMCTRL_STATUS_ERROR_SHIFT = 4      #number of bits to shift to get error to low bits
    NVMCTRL_STATUS_ERROR_MASK = 0b111   #mask after shifting to get error bits
    NVMCTRL_STATUS_BUSY_MASK = 0b11

    def __init__(self) -> None:
        pass

    def mapSectionAndGetAddress( self, updiLink:"UpdiLink", addr:int) -> int:
        #addr = flash-based address (0...32KB)
        #returns address of mapped flash data (0x8000 based)
        a = self.FLASH_START + addr
        assert a >= 0
        assert a <= 0xffff
        return a

    def getFlashPageSize(self) -> int:
        return self.FLASH_PAGE_SIZE

    def getFlashStart(self) -> int:
        return self.FLASH_START

    def writeCommandToNVMController(self,updiLink: "UpdiLink", cmd: int) -> None:
        debug("writeCommandToNVMController:",hex(cmd))

        #write key to configuration change protection register (CPU_START + offset 0x04)
        #page 40: Key for I/O registers is 0xd8. key for SPM is 0x9d.
        updiLink.STS(addr=self.CCP_ADDR, data=self.ALLOW_SPM )

        #write 'no-cmd' to ensure we're in a valid state for
        #issuing a new command
        updiLink.STS(addr=self.NVMCTRL_CONTROLA, data=cmd)

        self.checkNVMError(updiLink)

    def eraseFlash(self,updiLink: "UpdiLink") -> None:
        debug("erase flash")
        self.writeCommandToNVMController( updiLink, self.NVM_NOCMD )
        self.writeCommandToNVMController( updiLink, self.NVM_CHIP_ERASE)
        waitUntilTrue( lambda: False == self.getNVMControllerBusy(updiLink) )
        self.writeCommandToNVMController( updiLink, self.NVM_NOCMD )
        waitUntilTrue(lambda: False == self.getNVMControllerBusy(updiLink) )

    def getNVMAddr(self, updiLink: "UpdiLink") -> int:
        debug("getNVMAddr")
        addrBytes = [0,0,0]
        for i in range(self.NVMCTRL_ADDRESS_COUNT):
            tmp = updiLink.LDS(addr=self.NVMCTRL_ADDRESS + i)
            addrBytes[i] = tmp
        addr = addrBytes[0] | (addrBytes[1]<<8) | (addrBytes[2]<<16)
        debug("getNVMAddr: Address bytes:",addrBytes,"addr=",hex(addr))
        if addr > self.MAX_NVM_ADDRESS:
            error("NVM has gone bonkers")

        return addr

    def getNVMControllerStatus(self, updiLink: "UpdiLink") -> int:
        reg = updiLink.LDS(self.NVMCTRL_STATUS)
        return reg

    def getNVMControllerBusy(self, updiLink: "UpdiLink") -> bool:
        debugRep("getNVMControllerBusy")
        reg = self.getNVMControllerStatus(updiLink)
        e = (reg >> self.NVMCTRL_STATUS_ERROR_SHIFT) & self.NVMCTRL_STATUS_ERROR_MASK
        if e :
            error(f"NVM error: 0b{e:b}")
        return (reg & self.NVMCTRL_STATUS_BUSY_MASK) != 0

    def checkNVMError(self, updiLink: "UpdiLink") -> None:
        debug("checkNVMError")
        reg = updiLink.LDS(self.NVMCTRL_STATUS)
        e = (reg >> self.NVMCTRL_STATUS_ERROR_SHIFT) & self.NVMCTRL_STATUS_ERROR_MASK
        debug("NVMError reports:",bin(e))
        if e :
            error(f"NVM error: 0b{e:b}")

    def clearPageBuffer(self,updiLink:"UpdiLink") -> None:
        debug("clearPageBuffer")
        #the avr32dd20 does not have a page buffer
        self.writeCommandToNVMController( updiLink, self.NVM_NOCMD )

    def beginFlashWrite(self,updiLink: "UpdiLink") -> None:
        #ensure no ongoing write operations
        debug("beginFlashWrite")
        waitUntilTrue(lambda: False == self.getNVMControllerBusy(updiLink) )
        self.writeCommandToNVMController( updiLink, self.NVM_NOCMD )

    def beginFlashPage(self, updiLink: "UpdiLink", eraseAndWrite: bool, offset:int) -> None :
        debug("AVR32DD20: beginFlashPage at offset",offset)
        #the avr32dd does not have a page buffer, so we need not clear it
        #however, there is no "erase and write" command, so if we want to erase
        #the page, we must do that now as a separate command
        self.writeCommandToNVMController( updiLink, self.NVM_NOCMD )

        if eraseAndWrite:
            debug("Sending PAGE_ERASE command to NVM controller")
            self.writeCommandToNVMController(updiLink,self.NVM_PAGE_ERASE)
            debug("Storing dummy byte to set the address pointer: Store at",hex(offset),"bytes into flash")
            updiLink.STS(addr=self.FLASH_START + offset, data=0x00 )
            debug("Waiting until NVM controller no longer busy from dummy store")
            waitUntilTrue(lambda: False == self.getNVMControllerBusy(updiLink) )
            debug("Writing NOCMD to NVM controller")
            self.writeCommandToNVMController( updiLink, self.NVM_NOCMD )

        debug("Sending FLASH_WRITE command to NVM controller")
        self.writeCommandToNVMController(updiLink,self.NVM_FLASH_WRITE)

        return

    def endFlashPage(self, updiLink: "UpdiLink", eraseAndWrite:bool) -> None:
        debug("endFlashPage")
        debug("endFlashPage: addr=",hex(self.getNVMAddr(updiLink)))

        #nothing to do for eraseAndWrite

        self.writeCommandToNVMController( updiLink, self.NVM_NOCMD )

        #4ms for write, 6ms for erase+write
        time.sleep(0.006)
        ok = waitUntilTrue(lambda: False == self.getNVMControllerBusy(updiLink), maxTime=3.0 )
        if not ok:
            status = self.getNVMControllerStatus(updiLink)
            error(f"NVM controller always busy? {status:08b}")

    def storeToFlash(self,updiLink: "UpdiLink", offset:int, value:int) -> None:
        #write value to store in flash
        debug("store to flash at",hex(offset))
        updiLink.STS(self.FLASH_START+offset,value)

    def finishFlashWrite(self,updiLink: "UpdiLink") -> None:
        debug("finishFlashWrite")
        waitUntilTrue(lambda: False == self.getNVMControllerBusy(updiLink) )
        #write nocmd to complete operation
        self.writeCommandToNVMController(updiLink,self.NVM_NOCMD)
        #this probably isn't needed
        waitUntilTrue(lambda: not self.getNVMControllerBusy(updiLink) )


chips.registerChip("avr16dd14", lambda: AVR32DD20()  )
chips.registerChip("avr16dd20", lambda: AVR32DD20()  )
chips.registerChip("avr32dd14", lambda: AVR32DD20()  )
chips.registerChip("avr32dd20", lambda: AVR32DD20()  )
