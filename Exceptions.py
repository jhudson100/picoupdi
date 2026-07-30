from typing import Any

class ProgException(Exception):
    def __init__(self,msg:str,**kw:Any):
        self.msg=msg
        self.info={}
        for k in kw:
            self.info[k] = kw[k]
    def __repr__(self) -> str:
        ty = str(type(self))
        i1 = ty.find("'")
        i2 = ty.rfind("'")
        tname = ty[i1+1:i2]
        tmp = f"{tname}: {self.msg}"
        for k in self.info:
            x = self.info[k]
            tmp += f" {k}={repr(x)}"
        return tmp

    def __str__(self) -> str:
        return repr(self)

class CommandException(ProgException):
    def __init__(self,msg:str,**kw:Any) -> None:
        super().__init__(msg,**kw)

class Timeout(ProgException):
    def __init__(self,msg:str):
        super().__init__(msg)

class ProtocolError(ProgException):
    def __init__(self,msg:str):
        super().__init__(msg)


