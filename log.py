import sys
from typing import Any
import time

startTime = time.time()

DEBUG=False

def debug(*args: Any) -> None:
    if DEBUG:
        now = time.time()
        delta = now - startTime
        r = [str(q) for q in args]
        print(f"[{delta:.3f}]: "," ".join(r))



lastDebug = None
printedRepeated=False
def debugRep(*args: Any) -> None:
    if DEBUG:
        r = [str(q) for q in args]
        s = " ".join(r)

        global lastDebug,printedRepeated
        if lastDebug == s:
            if not printedRepeated:
                print("[[REPEATED]]")
                printedRepeated=True
            else:
                return
        else:
            lastDebug=s
            printedRepeated=False
            print(s)


def error(*args:Any) -> None:
    r = [str(q) for q in args]
    s = " ".join(r)
    print("ERROR:",s)
    if DEBUG:
        raise RuntimeError(s)
    else:
        sys.exit(1)

def warning(*args:Any) -> None:
    r = [str(q) for q in args]
    print("WARNING:"," ".join(r))
