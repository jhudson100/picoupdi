import sys
from abc import abstractmethod
import typing
from typing import Callable
if typing.TYPE_CHECKING:
    from UpdiLink import UpdiLink

class Chip:

    def __init__(self) -> None:
        pass

    # ~ @abstractmethod
    # ~ def setClock(self, updiLink:"UpdiLink", clock: int) -> None:
        # ~ raise NotImplementedError()

    @abstractmethod
    def getNVMAddr(self, updiLink:"UpdiLink") -> int:
        raise NotImplementedError()

    @abstractmethod
    def mapSectionAndGetAddress(self, updiLink: "UpdiLink", addr:int) -> int:
        raise NotImplementedError()

    @abstractmethod
    def getFlashStart(self) -> int:
        raise NotImplementedError()

    @abstractmethod
    def clearPageBuffer(self,updiLink:"UpdiLink") -> None:
        raise NotImplementedError()

    @abstractmethod
    def getNVMControllerBusy(self, updiLink: "UpdiLink") -> bool:
        raise NotImplementedError()

    @abstractmethod
    def getFlashPageSize(self) -> int:
        raise NotImplementedError()

    @abstractmethod
    def eraseFlash(self,updiLink: "UpdiLink") -> None:
        raise NotImplementedError()

    @abstractmethod
    def beginFlashWrite(self,updiLink: "UpdiLink") -> None:
        raise NotImplementedError()

    @abstractmethod
    def beginFlashPage(self, updiLink: "UpdiLink", eraseAndWrite: bool, offset:int) -> None :
        raise NotImplementedError()

    @abstractmethod
    def endFlashPage(self, updiLink: "UpdiLink", eraseAndWrite:bool) -> None:
        raise NotImplementedError()

    @abstractmethod
    def storeToFlash(self,updiLink: "UpdiLink", offset:int, value:int) -> None:
        raise NotImplementedError()

    @abstractmethod
    def finishFlashWrite(self,updiLink: "UpdiLink") -> None:
        raise NotImplementedError()


chips: dict[str,Callable[ [], Chip] ]={}
def registerChip( name:str, maker: Callable[ [], Chip]) -> None :
    assert name not in chips
    chips[name] = maker

def make(name:str) -> Chip|None:
    if name not in chips:
        return None
    return chips[name]()

def getChips() -> list[str]:
    return sorted(list(chips.keys()))
