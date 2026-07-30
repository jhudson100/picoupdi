#type:ignore

import machine
import time
import gc
import micropython
import rp2
import array
import json
import sys
import io
micropython.alloc_emergency_exception_buf(1024)

BACKEND_VERSION = const(0x20260508)

TIMEOUT_MS = 2000


TOTAL_BYTES_SENT=0
TOTAL_BYTES_RECEIVED=0

def getParity(i):
    p=0
    while i != 0:
        p ^= (i&1)
        i>>=1
        i &= 0x7f

    return p

parities = bytearray(256)
for i in range(256):
    parities[i] = getParity(i)

#update sent counter on put()
#update recv counter on get()
def updateSendCounter(ns):
    global TOTAL_BYTES_SENT
    TOTAL_BYTES_SENT += ns

def updateReceiveCounter(nr):
    global TOTAL_BYTES_RECEIVED
    TOTAL_BYTES_RECEIVED += nr

def clearCounters():
    global TOTAL_BYTES_SENT
    global TOTAL_BYTES_RECEIVED
    ns = TOTAL_BYTES_SENT
    nr = TOTAL_BYTES_RECEIVED
    TOTAL_BYTES_SENT=0
    TOTAL_BYTES_RECEIVED=0
    return (ns,nr)


#####################################################

#at default 4MHz clock, baud rate should
#be between 75bps and 225kbps
#Note that these numbers are only used for the initial
#updi activation, so they need not be high speed
BITS_PER_SECOND = const(1000)
BITS_PER_MICROSECOND = BITS_PER_SECOND / 1_000_000
MICROSECONDS_PER_BIT = 1/BITS_PER_MICROSECOND
BIT_TIME_US=int(MICROSECONDS_PER_BIT)
HALF_BIT_TIME_US = BIT_TIME_US >> 2

T_STATE_MACHINE = const(0)
R_STATE_MACHINE = const(1)


#this gets set to true when we've switched to PIO
usePio=False

def debug(*args):
    return
    tmp = [str(q) for q in args]
    tmp = " ".join(tmp)
    print("~!~<",repr(tmp),">~!~")

def setOutput():
    """Set the updi pin to output mode, high logic level"""
    if usePio:
        return
    updiPin.init(mode=machine.Pin.OUT,value=1)

def setInput():
    """Set the updi pin to input mode"""
    if usePio:
        return
    updiPin.init(mode=machine.Pin.IN)

class UPDIError(Exception):
    pass

class ParityError(UPDIError):
    def __init__(self,value,parity,stops):
        self.value=value
        self.parity=parity
        self.stops=stops
    def __str__(self):
        return repr(self)
    def __repr__(self):
        return f"Parity error: value={self.value:08b} parity={self.parity} stops={self.stops}"


class FrameError(UPDIError):
    def __init__(self,msg,start,value,parity,stop1,stop2):
        self.msg=msg
        self.start=start
        self.value=value
        self.parity=parity
        self.stop1=stop1
        self.stop2=stop2
    def __str__(self):
        return repr(self)
    def __repr__(self):
        return f"Frame error: {self.msg}: start={self.start} value={self.value:08b} parity={self.parity} stop1={self.stop1} stop2={self.stop2}"


class ProtocolError(UPDIError):
    def __init__(self,value):
        self.value=value
    def __str__(self):
        return repr(self)
    def __repr__(self):
        return f"Protocol error: value={self.value:08b} = {self.value}"

class TimeoutError(UPDIError):
    def __init__(self,msg=""):
        self.msg=msg
    def __str__(self):
        return repr(self)
    def __repr__(self):
        return f"Timeout error {self.msg} "

@micropython.native
def computeParity(B:int) -> int:
    return parities[B]

    # let B =   abcdefgh
    # C =           abcd
    # D = B^C = abcdijkl    where i=e^a, j=f^b, k=g^c, l=h^d
    # E =         abcdij
    # F = D^E         mn    where m = i^k, n=l^j
    # G =              m
    # H = F^G               m^n = i^k ^ l^j = e^a^g^c^h^d^f^b
    #If lsb of H is 0: We had even number of 1's.
    C=B>>4
    D=B^C
    E=D>>2
    F=D^E
    G=F>>1
    #H=F^G
    #return H&1
    return (F^G)&1


sendBuffer:array.array=array.array("I",[0])

def sendBreakPIO():

    flushSendPIO()
    flushRecvPIO()

    for i in range(2):
        # ~ debug("sendBreakPIO:",i)
        sendBuffer[0] = 0xffffffff
        sendStateMachine.put(sendBuffer)

        #dummy read to make sure state machine has set line low
        #0x08 is an arbitrary value to make sure we're in sync
        #with the state machine
        v = sendStateMachine.get()
        assert v == 0x08

        #the send state machine will set the line low and
        #wait for another message from us to reset the line high.
        #The datasheet recommends 25msec, done twice.
        time.sleep_ms(25)

        #this value is ignored; it just signals the PIO machine
        #to reset the line high. 0x13 is an arbitrary value
        #to make sure we're in sync with the state machine.
        sendStateMachine.put(sendBuffer[0])
        v = sendStateMachine.get()
        assert v == 0x13,f"expected 0x13 but got 0x{v:02x}"

        time.sleep_ms(25)
        flushRecvPIO()

@micropython.viper
def sendPIO(data:int):

    par: int = int(parities[data]) #cast for viper

    #parity calculation
    # ~ C=data>>4
    # ~ D=data^C
    # ~ E=D>>2
    # ~ F=D^E
    # ~ G=F>>1
    # ~ par:int = (F^G)&1

    val32:int = (par<<8) | data
    sendBuffer[0] = val32
    sendStateMachine.put(sendBuffer)
    updateSendCounter(1)

    #state machine will write a dummy value to its output buffer when done.
    nowDone = sendStateMachine.get()

    #debugging
    #assert nowDone == 0x12,f"0x{nowDone:02x}"

    #recv side will see the byte too, so we must pull it from the buffer.
    v = int(recvPIO1())	    #cast for viper


    #debugging
    #if v & 0xff != data:
    #    raise RuntimeError(f"sendPIO: When reading echo: got 0x{v:02x} expected 0x{data:02x}")

def flushRecvPIO():
    while recvStateMachine.rx_fifo() > 0:
        recvStateMachine.get()
        updateReceiveCounter(1)


def flushSendPIO():
    #flushes the "completed" messages from the send machine back to the CPU
    while sendStateMachine.rx_fifo() > 0:
        sendStateMachine.get()
        #no counter updates

def waitUntilTrue(predicate):
    start = time.ticks_ms()
    while True:
        if predicate():
            return
        if time.ticks_diff(time.ticks_ms(),start) >= TIMEOUT_MS:
            raise TimeoutError("waitUntilTrue")

Rbuff = array.array("I",[0]*32)

#high speed receive for a single byte
#note that we do not check parity bits
#but we do check the stop bits to detect framing errors.
@micropython.viper
def recvPIO1() -> int:

    v: int = int(recvStateMachine.get())  #cast for viper
    updateReceiveCounter(1)

    data: int = v & 0xff

    #inline parity; uncomment for more safety but
    #less speed
    #par: int = (v>>8) & 0x01
    # ~ C=data>>4
    # ~ D=data^C
    # ~ E=D>>2
    # ~ F=D^E
    # ~ G=F>>1
    # ~ epar = (F^G)&1
    # ~ if par != epar:
        # ~ raise ParityError(data,par)

    if (v & 0b11000000000) != 0b11000000000:
        raise FrameError("recvPIO1",0,data,-1,(v>>9)&1,v>>10)

    return data

#more flexible receive for multiple bytes
#Checks parity. If count is 1, return a single byte.
#If count > 1, return a bytearray.
@micropython.native
def recvPIO(count:int):

    if count == 1:
        waitUntilTrue( lambda: recvStateMachine.rx_fifo() > 0 )
        v = recvStateMachine.get()
        updateReceiveCounter(1)
        Rbuff[0]=v
    else:
        i=0
        while i < count:
            v = recvStateMachine.get()
            updateReceiveCounter(1)
            Rbuff[i]=v
            i+=1
        B = bytearray(count)

    for i in range(count):
        v = Rbuff[i]
        stops = v >> 9
        par = (v>>8) & 0x01
        data = v & 0xff
        epar = computeParity(data)

        if stops != 0b11:
            raise FrameError("recvPIO",0,data,par,stops&1,stops>>1)
        epar = computeParity(data)
        if par != epar:
            raise ParityError(data,par,stops)
        if count == 1:
            return data
        else:
            B[i] = data

    return B


#keep track of current parity value for sending a byte
parity=0

#low-speed send using CPU I/O
def sendBit(b):
    """Send one bit and update parity"""
    global parity
    if b:
        updiPin.high()
        parity ^= 1
    else:
        updiPin.low()
        parity ^= 0  #keep timing constant
    time.sleep_us(BIT_TIME_US)

#non-PIO send (low speed)
def sendDirect(b):
    """Send start bit, 8 data bits, parity bit, two stop bits"""

    global parity

    parity=0
    sendBit(0)  #start bit
    #we send bits lsb to msb
    sendBit(b&1)
    sendBit(b&2)
    sendBit(b&4)
    sendBit(b&8)
    sendBit(b&16)
    sendBit(b&32)
    sendBit(b&64)
    sendBit(b&128)
    sendBit(parity)
    sendBit(1)      #stop bit
    sendBit(1)      #stop bit

def sendSync():
    """Send a sync byte"""
    setOutput()
    #print("%%send sync")
    sendByte(0x55)

def waitAck():
    """Wait for an ack byte"""
    b = recvByte()
    if b != 0x40:
        raise ProtocolError()
    return b

def sendBreak():
    if usePio:
        sendBreakPIO()
    else:
        setOutput()
        updiPin.low()
        time.sleep_ms(25)
        updiPin.high()
        time.sleep_ms(1)
        updiPin.low()
        time.sleep_ms(25)
        updiPin.high()
        time.sleep_ms(1)

#low speed receive without using pio
def recvDirect():

    setInput()
    waitUntilTrue(lambda: recvPin.value() == 0 )
    start = recvPin.value()

    #has gone low, so we are looking at start bit
    #delay for one bit period + one quarter of bit time
    #to ensure we're not right on the edge
    time.sleep_us(BIT_TIME_US+HALF_BIT_TIME_US)

    v=0
    i=0
    exppar = 0
    while i < 8:
        b = recvPin.value()
        v >>= 1
        if b:
            v |= 0x80
        exppar ^= b
        time.sleep_us(BIT_TIME_US )
        i+=1

    par = recvPin.value()
    time.sleep_us(BIT_TIME_US )

    stop1 =  recvPin.value()
    time.sleep_us(BIT_TIME_US )

    stop2 = recvPin.value()
    #don't sleep after second stop bit

    if start != 0 or stop1 != 1 or stop2 != 1:
        raise FrameError("recvDirect",start,v,par,stop1,stop2)

    if exppar != par:
        raise ParityError(v,par,(stop1<<1)|stop2)

    return v


def sendByte(b):

    # ~ debug("send byte",hex(b))

    if usePio:
        sendPIO(b)
    else:
        sendDirect(b)

def recvByte(count=1):
    if usePio:
        v = recvPIO(count)
    else:
        assert count==1
        v = recvDirect()
    # ~ debug("recvByte",count,": got",v)
    return v

#works?!?
# def recvByte(count=1):
#     if usePio:
#         b = recvPIO(count)
#     else:
#         assert count==1
#         b = recvDirect()
#     #print("recvByte:",b)
#     return b



def ST(ptrType, dataSize, data:int):
    """Store to memory location. If data is two or three bytes, send lsB first"""
    sendSync()
    sendByte(0b0110_0000 | (ptrType<<2) | (dataSize) )

    #dataSize is 00 (for one byte), 01 (for two bytes), or 10 (for three bytes)
    #Thus, we add one for the loop count
    for i in range(dataSize+1):
        sendByte( data & 0xff )
        data >>= 8
    waitAck()


def LDCS(addr):
    """load (read) control/status register"""
    sendSync()
    sendByte( 0b1000_0000 | addr )
    d = recvByte()
    return d

def STCS(addr,data:int):
    """store (write) control/status register"""
    sendSync()
    sendByte( 0b1100_0000 | addr )
    sendByte(data)
    setInput()

def REPEAT(count):
    # ~ debug("REPEAT",count)
    sendSync()
    sendByte(0b1010_0000 | SIZE_BYTE)
    sendByte( (count-1) & 0xff)

def enableUpdi():
    global updiPin

    #initialize updi system for communication

    setOutput()
    time.sleep_ms(100)         #let line settle and sync with avr


    updiPin.low()
    time.sleep_us(100)
    updiPin.high()

    #Release line. UPDI will hold it low until it's ready for comm,
    #but this is so fast that we don't really need to do anything here
    #to check for it. Maybe add a quick sleep_us here?
    setInput()

    #Then UPDI releases line and pull-up brings it high
    #Within 200us - 13.5ms, send a sync character to initiate transmission
    sendSync()

    #ensure avr is not stuck in some previous transaction
    sendBreak()

    setInput()

    return



def startPio():
    global usePio
    sendStateMachine.active(0)
    recvStateMachine.active(0)
    sendStateMachine.restart()
    recvStateMachine.restart()
    sendStateMachine.active(1)
    recvStateMachine.active(1)
    usePio=True

def stopPio():
    global usePio
    usePio=False
    sendStateMachine.active(0)
    recvStateMachine.active(0)

SIZE_BYTE = 0b00
SIZE_WORD = 0b01
PTR_DEREF_POSTINC = 0b01


@micropython.native
def sendBlockFastByte(block:bytes|bytearray, parity:bytes|bytearray):
    #block is a chunk of 1...256 bytes of data
    #parity is a chunk of parity bits, stored as the 9th bit (so the low
    #8 bits are zeros)

    #if updi's pointer has not been initialized,
    #the caller must call:
    #   ST(PTR_ITSELF, SIZE_WORD, addr )
    #before calling this function. addr is the
    #destination address for the transfer. Since this
    #function uses ST(PTR_DEREF_POSTINC) to start the
    #transfer, sendBlock() may be called repeatedly
    #without updating ptr in the meantime.

    le = len(block)
    REPEAT(le)
    ST(PTR_DEREF_POSTINC, SIZE_BYTE, block[0])
    i=1
    ok=True
    while i < le:

        #send via PIO

        #val32:int = (par<<8) | data
        sendBuffer[0] = block[i] | (parity[i]<<8)
        sendStateMachine.put(sendBuffer)

        #state machine will write a dummy value (0x12) to
        #its output buffer to signal it has completed the send
        sendStateMachine.get()


        #recv side will see the byte we just sent,
        #so we must pull it from the buffer
        #and discard it
        recvStateMachine.get()

        #sendPIO(block[i])

        #response from updi.
        #v includes stop bits and parity bit
        #should be 111 0100 0000
        #(value=0x40, parity=1, stop=11)
        v: int = recvStateMachine.get()
        if v != 0b111_0100_0000:
            ok=False
        i+=1

    #-1 because the ST() counted one of them already
    updateSendCounter(len(block)-1)
    updateReceiveCounter(len(block)-1)
    if not ok:
        raise ProtocolError()

    flushRecvPIO()



@micropython.native
def sendBlockFastWord(block:bytes|bytearray, parity:bytes|bytearray):
    #block is a chunk of 1...256 bytes of data
    #parity is a chunk of parity bits, stored as the 9th bit (so the low
    #8 bits are zeros)

    #if updi's pointer has not been initialized,
    #the caller must call:
    #   ST(PTR_ITSELF, SIZE_WORD, addr )
    #before calling this function. addr is the
    #destination address for the transfer. Since this
    #function uses ST(PTR_DEREF_POSTINC) to start the
    #transfer, sendBlock() may be called repeatedly
    #without updating ptr in the meantime.

    le = len(block)
    if le == 0:
        return

    #send one word at a time. Divide le by two since we're doing word stores
    numRepeats = le>>1

    #index of next byte to send
    i=0

    if numRepeats > 1:
        REPEAT(numRepeats)
        #lsB first, then msB
        ST(PTR_DEREF_POSTINC, SIZE_WORD, block[0] | ( block[1] << 8) )
        i=2     #already sent two bytes
        numRepeats -= 1  #already did the first one
        ok=True
        while numRepeats > 0:

            #send via PIO. Do one word at a time.
            #Since we indicated we're doing a word store above,
            #we only get an ack back after the second byte.
            for j in range(2):

                #state machine always sends only one byte at a time,
                #so we must do two put's
                sendBuffer[0] = block[i] | (parity[i]<<8)
                sendStateMachine.put(sendBuffer)
                i+=1

                #state machine will write a dummy value (0x12) to
                #its output buffer to signal it has completed the send
                sendStateMachine.get()

                #recv side will see the byte we just sent,
                #so we must pull it from the buffer
                #and discard it
                recvStateMachine.get()

            updateSendCounter(2)

            #response from updi.
            #v includes stop bits and parity bit
            #should be 111 0100 0000
            #(value=0x40, parity=1, stop=11)
            v: int = recvStateMachine.get()
            if v != 0b111_0100_0000:
                ok=False
            updateReceiveCounter(1)
            numRepeats -= 1

    if i < le:
        #if we had an odd number of bytes, store the last one without using repeat.
        ST(PTR_DEREF_POSTINC, SIZE_BYTE, block[le-1] )
        sendStateMachine.get()      #ack that we've sent it
        recvStateMachine.get()      #byte that we just sent
        v: int = recvStateMachine.get()     #resp from avr
        if v != 0b111_0100_0000:
            ok=False

    if not ok:
        raise ProtocolError()

    flushRecvPIO()



rbfBuffer = array.array("I",[0]*256)


@micropython.native
def recvBlockFastWord(count:int):
    #Assume that count is even; load data one word at a time

    #if updi's pointer has not been initialized,
    #the caller must call:
    #   ST(PTR_ITSELF, SIZE_WORD, addr )
    #before calling this function. addr is the
    #source address for the transfer. Since this
    #function uses LD(PTR_DEREF_POSTINC) to start the
    #transfer, recvBlockFast() may be called repeatedly
    #without updating ptr in the meantime.
    #count must be > 0 and <= 256 and it must be even.

    count &= ~1     #make sure it's even

    if count == 0:
        return rbfBuffer

    REPEAT(count>>1)        #doing word receive

    sendPIO(0x55)  #sync
    #LD command, no ack
    sendPIO(0b0010_0000 | (PTR_DEREF_POSTINC<<2) | SIZE_WORD )
    i=0
    while i<count:
        #we don't do parity or framing checks here;
        #that's left for the host
        rbfBuffer[i] = recvStateMachine.get()
        i+=1

    #sendPIO updates the counters itself
    updateReceiveCounter(count)

    return rbfBuffer


@micropython.native
def recvBlockFastByte(count:int):

    #if updi's pointer has not been initialized,
    #the caller must call:
    #   ST(PTR_ITSELF, SIZE_WORD, addr )
    #before calling this function. addr is the
    #source address for the transfer. Since this
    #function uses LD(PTR_DEREF_POSTINC) to start the
    #transfer, recvBlockFast() may be called repeatedly
    #without updating ptr in the meantime.
    #count must be > 0 and <= 256
    # We do byte receives here since there are no ack's;
    # it's not expected that doing word receives would
    # improve the speed significantly.

    B=bytearray(count)
    REPEAT(count)

    sendPIO(0x55)  #sync
    #LD command, no ack
    sendPIO(0b0010_0000 | (PTR_DEREF_POSTINC<<2) | SIZE_BYTE )
    i=0
    while i<count:
        #we don't do parity or framing checks here;
        #that's left for the host
        rbfBuffer[i] = recvStateMachine.get()
        i+=1

    #sendPIO updates the counters itself
    updateReceiveCounter(count)

    return rbfBuffer

def error(msg,**kw):
    D = { "ok": False, "reason": msg }
    for key in kw:
        D[key] = kw[key]
    J = json.dumps(D)
    sys.stdout.write(J)
    sys.stdout.write("\n")

def output(**kw):
    D = { "ok": True}
    for key in kw:
        D[key] = kw[key]
    J = json.dumps(D)
    sys.stdout.write(J)
    sys.stdout.write("\n")

def main():


    global sendStateMachine, recvStateMachine
    global updiPin, recvPin
    global usePio
    global TIMEOUT_MS

    GPIO_NUMBER = None
    PIO_FREQ=2_048_000

    machine.freq(125_000_000)

    connected=False

    sys.stdout.write("\nPICO UPDI BACKEND\n")

    while True:
        line = input("@@>")
        line=line.strip()
        if len(line) == 0:
            continue

        try:
            J = json.loads(line)

            action = J.get("action")
            if not action:
                error("No action specified")
            elif action == "GET_VERSION":
                output(version=BACKEND_VERSION)
            elif action == "SET_TIMEOUT":
                TIMEOUT_MS = int(J["value"])
                output()
            elif action == "CONNECT_UPDI":
                if connected:
                    error("Already connected")
                else:
                    GPIO_NUMBER = J["gpio"]
                    #GPIO_R_NUMBER = J["receive"]
                    pf = J.get("pioFrequency",0)
                    if pf > 0:
                        PIO_FREQ = int(pf)
                    updiPin = machine.Pin(GPIO_NUMBER,machine.Pin.OUT,value=1)
                    gc.disable()
                    try:
                        enableUpdi()
                        #enableInterbyteDelay()
                    finally:
                        gc.enable()

                    #after this point, no UPDI exceptions
                    #can be thrown, so we can indicate
                    #that we are connected
                    connected=True
                    sendStateMachine = rp2.StateMachine(
                        T_STATE_MACHINE, sendPioAsm, freq=PIO_FREQ,
                        out_base=updiPin, set_base=updiPin
                    )
                    recvStateMachine = rp2.StateMachine(
                        R_STATE_MACHINE,recvPioAsm,freq=PIO_FREQ,
                        in_base=updiPin #recvPin
                    )
                    startPio()
                    output()
            elif action == "DISCONNECT_UPDI":
                if connected:
                    stopPio()
                    setInput()
                    #recv pin is already input
                    connected=False
                    output()
                else:
                    #this isn't an error; it's valid
                    #to disconnect when already disconnected,
                    #but it's a no-op
                    output(message="Not connected")
            elif action == "BREAK":
                if not connected:
                    error("Not connected")
                else:
                    sendBreak()
                    output()
            elif action == "DATA":
                if not connected:
                    error("Not connected")
                else:
                    v=[]
                    numExpected = J["numExpected"]

                    for b in J["toSend"]:
                        sendByte(b)
                    v = recvByte(numExpected)
                    if numExpected == 0:
                        v=None
                    elif numExpected == 1:
                        v=[v]
                    else:
                        v = list(v)
                    output(data=v, numExpected=numExpected)
            elif action == "SEND_BLOCK":
                if not connected:
                    error("Not connected")
                elif len(J["data"]) < 1 or len(J["data"]) > 256:
                    error("Bad data size")
                else:
                    sendBlockFastByte(bytes(J["data"]), bytes(J["parity"]) )
                    output()
            elif action == "SEND_BLOCK_WORD":
                if not connected:
                    error("Not connected")
                elif len(J["data"]) < 1 or len(J["data"]) > 256:
                    error("Bad data size")
                else:
                    sendBlockFastWord(bytes(J["data"]), bytes(J["parity"]) )
                    output()
            elif action == "RECV_BLOCK_BYTE" or action == "RECV_BLOCK_WORD":
                count = J["count"]
                if action == "RECV_BLOCK_WORD" and count & 1:
                    error("Count must be even")

                if not connected:
                    error("Not connected")
                elif count < 1 or count > 256:
                    error("Bad count")
                else:
                    #parity and stop bits are part of returned data
                    #data is an array.array of u32's
                    if action == "RECV_BLOCK_BYTE":
                        data = recvBlockFastByte(count)
                    else:
                        data = recvBlockFastWord(count)
                    output(data=list(data[0:count]))
            elif action == "STATS":
                ns,nr = clearCounters()
                output(sent=ns,received=nr)
            else:
                error("Bad commmand")
        except Exception as e:
            etype = str(type(e))
            sio = io.StringIO()
            sys.print_exception(e,sio)
            error(etype,error=str(e),traceback=sio.getvalue())
            sio=None
            gc.collect()



#sends lsb first.
#Each bit takes 16 ticks of the PIO clock + 1 more for the PIO instruction itself
@rp2.asm_pio(set_init=rp2.PIO.OUT_HIGH, out_init=rp2.PIO.OUT_HIGH,out_shiftdir=rp2.PIO.SHIFT_RIGHT)
def sendPioAsm():

    label("start")

    wrap_target()

    #ensure pin is input when idle
    set(pindirs, 0)


    #We will send 8 data bits and 1 parity bit.
    #The value is tested before decrementing, so
    #we initialize to one less than the number to
    #be sent
    set(x, 8)

    # get 32 bits of data from the transmit fifo
    # to the output shift register, blocking if there is
    # none available. We send the low 9 bits (8 data +
    # 1 parity)
    pull(block)

    #set pin to be high and output. Since
    #avr has pull-up, line should be already
    #have been high when idle, so we need not delay here
    set(pins,1)
    set(pindirs,1)

    #send start bit: Set pin low and wait one bit period
    #subtract 2 here because of the following two instructions
    set(pins,0).delay(14)

    #see if a break is requested: if 0xffffffff was sent to us,
    #the inversion of it will be zero and we'll jump to the target
    mov(y,invert(osr))
    jmp(not_y,"sendBreak")

    label("sendBit")

    #get one bit from the output shift register and
    #output it. Also delay for one bit period.
    #we subtract one because of the jmp
    out(pins, 1).delay(15)

    #send rest of bits
    jmp(x_dec,"sendBit")

    #stop bits. We only delay for one bit period
    #since the rest of the program will likely be slow enough that
    #the second bit period will have elapsed in the meantime.
    #If the remaining program is running very quickly,
    #can uncomment the second set() to delay a second bit period.
    set(pins,1).delay(16)
    #set(pins,1).delay(16)

    #signal that we're done by pushing data to the cpu
    #0x12 is just a convenient flag value that fits in 5 bits.
    set(x,0x12)
    mov(isr,x)
    push(block)

    #if we get here, we've sent all the data.
    jmp("start")

    label("sendBreak")

    #if we get here, the output pins are zero
    #AVR datasheet recommends 25ms delay.
    #At 2MHz PIO clock, that's 50,000 PIO cycles.
    #With 32 cycles per instruction (1 instr + 31 delay),
    #we need 1562 instructions. Rather than set up a nested
    #loop here with the y register, we'll wait for the
    #cpu to send us another data item in the transmit fifo
    #as a signal to stop the break.

    #send arbitrary value to cpu code (x has value 8 here,
    #so caller should see 8)
    mov(isr,x)
    push(block)

    #when CPU wants us to release the break, it will send us another
    #message. We don't care about the value of it.
    pull(block)

    #reset the pins to 1
    set(pins,1).delay(16)

    #no need to discard isr contents; they will be
    #overwritten with the next pull()

    #send another value to cpu code to signal that we've
    #set the pins high. 0x13 is just a convenient dummy value.
    set(x,0x13)
    mov(isr,x)
    push(block)

    wrap()



#Each bit takes 16 ticks of the PIO clock + 1 more for the PIO instruction itself
#we set the fifo to combined mode so we can buffer 8 bytes from the AVR
@rp2.asm_pio(in_shiftdir=rp2.PIO.SHIFT_RIGHT,fifo_join=rp2.PIO.JOIN_RX)
def recvPioAsm():

    wrap_target()

    #11 bits not counting start bit:
    # 8 data, 1 parity, 2 stop, but don't
    #delay after the second stop bit.
    #The x value is tested before decrementing, so
    #we initialize to 9 (8 data + 1 parity + 1 stop - 1)
    set(x,9)

    #start bit:
    #wait for a zero on our pin
    #After instruction completes, wait for
    #one bit time (16) and then half a bit time (8)
    #so we will sample in the middle of a bit period
    wait(0, pin, 0).delay(24)

    label("recvBit")

    #get one bit from pins, send to input shift register
    #(ISR).
    #Delay is 15 cycles here because
    #the jmp (next instruction) takes 1 cycle
    in_(pins,1).delay(15)

    #if x is zero we're done
    #else, decrement x and get another bit
    jmp(x_dec,"recvBit")

    #get the last stop bit and don't delay after it
    in_(pins,1)

    #when we get here, we've read all the data.
    #align the data to the low part of the register
    in_( null, 21 )

    #push the isr to the output fifo and clear
    #the isr
    push()

    wrap()



main()
