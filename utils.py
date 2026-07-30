from typing import Callable
import time
from Exceptions import Timeout

TIMEOUT=10.0
def waitUntilTrue(predicate: Callable[[],bool], maxTime:float|int|None=None) -> bool:
    if maxTime == None:
        deadline = time.time() + TIMEOUT
    else:
        deadline = time.time() + maxTime
    
    while True:
        if predicate():
            return True
        if time.time() >= deadline:
            if maxTime == None:
                raise Timeout("Timed out waiting for condition")
            else:
                return False


 

