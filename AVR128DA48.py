import time
import chips
from typing import Any,Callable
from typing import NamedTuple
from log import debug,error
import typing
from UpdiLink import UpdiLink
from utils import waitUntilTrue
import AVR64DB32

class AVR128DA48(AVR64DB32.AVR64DB32):

    MAX_NVM_ADDRESS = 0x1ffff

    def __init__(self) -> None:
        super().__init__()

    def beginFlashPage(self, updiLink: "UpdiLink", eraseAndWrite: bool, offset:int) -> None :
        debug("beginFlashPage")
        #the avr32dd does not have a page buffer, so we need not clear it
        #however, there is no "erase and write" command, so if we want to erase
        #the page, we must do that now as a separate command
        self.writeCommandToNVMController( updiLink, self.NVM_NOCMD )
        assert offset % self.PAGE_SIZE == 0
        assert offset >= 0
        assert offset < 131072
        self.mapFlashRange(updiLink, offset >> 15 )
        offset %= 32768
        super().beginFlashPageNoMap(updiLink,eraseAndWrite,offset)


chips.registerChip("avr128da28", lambda: AVR128DA48()  )
chips.registerChip("avr128da32", lambda: AVR128DA48()  )
chips.registerChip("avr128da48", lambda: AVR128DA48()  )
chips.registerChip("avr128da64", lambda: AVR128DA48()  )
