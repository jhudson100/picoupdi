import time
import chips
from typing import Any,Callable
from typing import NamedTuple
from log import debug,error
import typing
from UpdiLink import UpdiLink
from utils import waitUntilTrue

class AVR16DB14(chips.Chip):
    NAME="AVR16DB14"
    NVM_NOCMD = 0x00
    NVM_CHIP_ERASE = 0x20 #chip erase
    NVM_PAGE_ERASE_AND_WRITE = 0x05
    NVM_PAGE_ERASE = 0x08
    NVM_PAGE_WRITE = 0x04     #no erase
    NVM_CLEAR_PAGE_BUFFER = 0x0f

    #ref: Peripheral Module Address Map tells address of peripherals
    CPU_START = 0x0030
    NVMCTRL_START = 0x1000
    #ref: Memories chapter tells start for eeprom, etc.
    FLASH_START = 0x8000        #up to 32KB flash
    EEPROM_START = 0x1400
    SIGROW_START = 0x1080
    FUSE_START = 0x1050
    FLASH_PAGE_SIZE=64

    #other values
    CCP_ADDR = CPU_START + 0x04      #location to write for config change protection
    ALLOW_SPM = 0x9d                 #allow storing to flash (store prog mem)

    NVMCTRL_CONTROLA = NVMCTRL_START + 0x00

    NVMCTRL_ADDRESS = NVMCTRL_START + 0x0c
    NVMCTRL_ADDRESS_COUNT = 3       #number of bytes in nvm_address

    NVMCTRL_STATUS = NVMCTRL_START + 0x06       #status: busy bit
    NVMCTRL_STATUS_ERROR_SHIFT = 4      #number of bits to shift to get error to low bits
    NVMCTRL_STATUS_ERROR_MASK = 0b111   #mask after shifting to get error bits
    NVMCTRL_STATUS_BUSY_MASK = 0b11

    def __init__(self) -> None:
        pass

    def mapSectionAndGetAddress( self, updiLink:"UpdiLink", addr:int) -> int:
        #addr = flash-based address (0...32KB)
        #returns address of mapped flash data (0x8000 based)
        return self.FLASH_START + addr

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
        self.writeCommandToNVMController( updiLink, self.NVM_NOCMD )
        self.writeCommandToNVMController( updiLink, self.NVM_CHIP_ERASE)
        waitUntilTrue( lambda: False == updiLink.getNVMControllerBusy() )
        self.writeCommandToNVMController( updiLink, self.NVM_NOCMD )
        waitUntilTrue(lambda: False == updiLink.getNVMControllerBusy() )

    def getNVMAddr(self, updiLink: "UpdiLink") -> int:
        addr=0
        shift=0
        for i in range(self.NVMCTRL_ADDRESS_COUNT):
            tmp = updiLink.LDS(addr=self.NVMCTRL_ADDRESS + i)
            tmp <<= shift
            addr |= tmp
            shift +=8
        return addr

    def getNVMControllerBusy(self, updiLink: "UpdiLink") -> bool:
        reg = updiLink.LDS(self.NVMCTRL_STATUS)
        e = (reg >> self.NVMCTRL_STATUS_ERROR_SHIFT) & self.NVMCTRL_STATUS_ERROR_MASK
        if e :
            error(f"NVM error: 0b{e:b}")
        return (reg & self.NVMCTRL_STATUS_BUSY_MASK) != 0

    def checkNVMError(self, updiLink: "UpdiLink") -> None:
        reg = updiLink.LDS(self.NVMCTRL_STATUS)
        e = (reg >> self.NVMCTRL_STATUS_ERROR_SHIFT) & self.NVMCTRL_STATUS_ERROR_MASK
        if e :
            error(f"NVM error: 0b{e:b}")

    def beginFlashWrite(self,updiLink: "UpdiLink") -> None:
        #ensure no ongoing write operations
        debug("beginFlashWrite")
        waitUntilTrue(lambda: False == updiLink.getNVMControllerBusy() )
        self.writeCommandToNVMController( updiLink, self.NVM_NOCMD )
        self.checkNVMError(updiLink)

    def clearPageBuffer(self,updiLink:"UpdiLink") -> None:
        debug("CLEAR PAGE BUFFER")
        self.checkNVMError(updiLink)
        self.writeCommandToNVMController( updiLink, self.NVM_CLEAR_PAGE_BUFFER )
        #datasheet does not specify how long this takes
        #ATMega4808 required 7uS at 1MHz CPU speed.
        #Overhead of serial communication should account for this
        self.checkNVMError(updiLink)
        self.writeCommandToNVMController( updiLink, self.NVM_NOCMD )
        # TODO: Do we need to send a "begin flash write" command?

    def beginFlashPage(self, updiLink: "UpdiLink", eraseAndWrite: bool, offset:int) -> None :
        debug("beginFlashPage")
        self.clearPageBuffer(updiLink)
        #we don't use eraseAndWrite here; that decision is made
        #when the page has been ended

    def endFlashPage(self, updiLink: "UpdiLink", eraseAndWrite:bool) -> None:
        debug("endFlashPage: addr=",hex(self.getNVMAddr(updiLink)))

        if eraseAndWrite:
            #erase and write page
            self.writeCommandToNVMController(updiLink,self.NVM_PAGE_ERASE_AND_WRITE)
        else:
            #just write page; no erase
            self.writeCommandToNVMController(updiLink,self.NVM_PAGE_WRITE)

        #4ms for write, 6ms for erase+write
        time.sleep(0.006)
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


chips.registerChip("avr16eb14", lambda: AVR16DB14()  )
chips.registerChip("avr16eb20", lambda: AVR16DB14()  )
chips.registerChip("avr16eb28", lambda: AVR16DB14()  )
chips.registerChip("avr16eb32", lambda: AVR16DB14()  )
