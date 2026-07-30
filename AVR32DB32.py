import time
import chips
from typing import Any,Callable
from typing import NamedTuple
from log import debug,error
import typing
from UpdiLink import UpdiLink
from utils import waitUntilTrue
import AVR32DD20

class AVR32DB32(AVR32DD20.AVR32DD20):
    pass


chips.registerChip("avr32db28", lambda: AVR32DB32()  )
chips.registerChip("avr32db32", lambda: AVR32DB32()  )
chips.registerChip("avr32db48", lambda: AVR32DB32()  )
