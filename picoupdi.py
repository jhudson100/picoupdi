#!/usr/bin/env python

# pip install --user --upgrade pyserial

# python3 frontend.py --gpio 12,13 --speed 200000  write test.bin


import serial                       #type: ignore
import serial.tools.list_ports      #type: ignore
import argparse
import sys
import time
import json
import struct
import io
from typing import Any,Callable
from typing import NamedTuple
from Exceptions import ProtocolError,Timeout
from UpdiLink import UpdiLink
from SerialPort import SerialPort
import PicoLink
from log import debug,error

import chips
import ATmega4808
import AVR16EB14
import AVR32DD20
import AVR32DB32
import AVR64DB32
import AVR128DA48


from UpdiLink import SIB_OP_SEND_KEY, KEY_SIZE_64_BITS, SIB_OP_RECV_SIB, KEY_SIZE_128_BITS, PTR_ITSELF, SIZE_WORD
# ~ TIMEOUT=10.0
# ~ def waitUntilTrue(predicate: Callable[[],bool]) -> None:
    # ~ deadline = time.time() + TIMEOUT
    # ~ while True:
        # ~ if predicate():
            # ~ return
        # ~ if time.time() >= deadline:
            # ~ raise Timeout("Timed out waiting for condition")


def sendKey(updiLink: UpdiLink,key:list[int]) -> None:
    updiLink.KEY(SIB_OP_SEND_KEY,KEY_SIZE_64_BITS,key)

def recvSib(updiLink: UpdiLink) -> list[int]:
    debug("~~~~~~~~~~~~recvSib: request sib")
    #0x55, 0xe5
    #  1110 0101
    sib = updiLink.KEY(SIB_OP_RECV_SIB, KEY_SIZE_128_BITS,[],16)
    #debug("~~~~~~~~~~~~recvSib: receive bytes")
    #sib = updiLink.recvBytes(16)
    debug("got sib=",sib)
    return sib


def getErrorStatus(updiLink: UpdiLink) -> int:
    #status B register
    v = updiLink.LDCS(0x01)
    return v


def getSignature(updiLink: UpdiLink) -> tuple[int,int,int]:
    #signature row: pg 45: First three bytes are signature
    #signature row is at 0x1080
    srow0 = updiLink.LDS(0x1080)
    srow1 = updiLink.LDS(0x1081)
    srow2 = updiLink.LDS(0x1082)
    return (srow0,srow1,srow2)

def enableInterbyteDelay(updiLink: UpdiLink) -> None :
    v = updiLink.LDCS(0x02)
    v |= (1<<7)
    updiLink.STCS(0x02, v )

def disableInterbyteDelay(updiLink: UpdiLink) -> None :
    v = updiLink.LDCS(0x02)
    v &= ~(1<<7)
    updiLink.STCS(0x02, v )

def beginFlashWrite(updiLink: UpdiLink) -> None:
    # ~ debug("beginFlashWrite")
    updiLink.chip.beginFlashWrite(updiLink)

def storeToFlash(updiLink: UpdiLink, offset:int, value:int) -> None:
    updiLink.chip.beginFlashWrite(updiLink)
    updiLink.chip.clearPageBuffer(updiLink)
    #don't erase since we only change one byte
    updiLink.chip.beginFlashPage(updiLink,False,offset)
    updiLink.chip.storeToFlash(updiLink,offset,value)
    #don't erase since we only want to change one byte
    updiLink.chip.endFlashPage(updiLink,False)
    updiLink.chip.finishFlashWrite(updiLink)



def eraseFlash(updiLink: UpdiLink) -> None:
    updiLink.chip.eraseFlash(updiLink)

def readFlash(updiLink: UpdiLink, start: int, count: int) -> list[int]:
    tmp=[]
    for i in range(count):
        tmp.append(updiLink.LDS(updiLink.chip.getFlashStart()+start+i))
    return tmp

#documented in UPDI CONTROLA register
#0x0 = 128 cycles of guard time
#0x1 = 64 cycles of guard time
#0x6 = 2 cycles of guard time
#0x7 = No guard time
def setGuardTime(updiLink: UpdiLink,g:int) -> None:
    c = updiLink.LDCS(0x02)
    c &= ~0b111
    c |= g
    updiLink.STCS(0x02, c)

def countOnes(n:int) -> int:
    no = 0
    while n != 0:
        if n&1:
            no+=1
        n>>=1
    return no

parities=[]
def makeParities() -> None:
    for i in range(256):
        if countOnes(i) % 2 :
            parities.append(1)
        else:
            parities.append(0)

makeParities()


def readCompletely( fp: io.BufferedReader, count: int, notPast: int ) -> bytearray:
    """Read up to count bytes from fp, but not past file offset notPast"""
    result = bytearray()
    endOffset = min([ notPast, fp.tell() + count ] )
    count = endOffset - fp.tell()
    while count > 0:
        tmp = fp.read(count)
        if len(tmp) == 0:
            break
        result += tmp
        count -= len(tmp)
    return result


def sendFile(updiLink: UpdiLink,filename: str,
    destinationOffset:int,
    skipUnchanged: bool,
    eraseBeforeWrite:bool,
    sizeLimit:int,
    sourceSkip:int,
    chipHasBeenErased: bool,
    verify: bool) -> None:

    debug(f"<><>sendFile: Begin writing at most {sizeLimit} bytes at offset {destinationOffset}")

    numWritten = 0

    if chipHasBeenErased:
        debug("Chip was erased, so forcing eraseBeforeWrite to False")
        eraseBeforeWrite = False

    chip = updiLink.chip

    if 0 != destinationOffset % chip.getFlashPageSize():
        error("Destination offset must be a multiple of page size (",chip.getFlashPageSize()," bytes)")

    ns,nr = updiLink.picoLink.STATS()

    beginFlashWrite(updiLink)

    startTime = time.time()
    with open(filename,"rb") as fp:
        fp.seek(0,2)
        fsize = fp.tell() - sourceSkip
        if fsize < 0:
            fsize = 0
        fp.seek(sourceSkip)

        while True:
            debug("~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~")
            numRead = fp.tell() - sourceSkip

            if numRead >= sizeLimit:
                print("Reached size limit; stopping")
                break

            if skipUnchanged:
                if chipHasBeenErased:
                    #we know flash is all 0xff's
                    debug("Chip was erased; assuming flash is all 0xff")
                    currentFlash = bytearray([0xff]*chip.getFlashPageSize())
                else:
                    debug("Getting current flash")
                    currentFlash = streamRead( updiLink,
                        chip.getFlashPageSize(),
                        destinationOffset + numRead
                    )
            else:
                currentFlash = bytearray()


            originalFileData=readCompletely( fp, chip.getFlashPageSize(), sourceSkip + sizeLimit )

            if len(originalFileData) == 0:
                #no more data to read
                break

            targetAddress = destinationOffset + numRead
            assert targetAddress % chip.getFlashPageSize() == 0
            assert len(originalFileData) <= chip.getFlashPageSize()

            if skipUnchanged and currentFlash[:len(originalFileData)] == originalFileData:
                changed="[Unchanged]"
            else:
                parity=[]
                for b in originalFileData:
                    parity.append( parities[b] )

                chip.beginFlashPage(updiLink,eraseBeforeWrite,targetAddress)

                #if chip has >32KB of flash, we must map the section we want to write
                #into the chip's writeable window of addresses. This will give us a different
                #destination address than the physical address.
                addrToWrite = chip.mapSectionAndGetAddress( updiLink, targetAddress )
                debug("send_file: sendBlock will ST to",hex(addrToWrite),"...",hex(addrToWrite+len(originalFileData)-1))

                #debugging
                ab = chip.getNVMAddr(updiLink)
                debug("before sending: nvm addr is:",hex(ab))

                #strange issue with AVR64DBxx: Writing close to RAM address 0xffff
                #(which is either flash address 0x7fff or 32K+0x7fff)
                #causes NVM controller to start giving back bogus values
                #from then on for address and status registers.
                # Workaround: stop early and use STS to do the last few bytes
                RED_ZONE_START = 0xfffc
                numInRedZone = addrToWrite + len(originalFileData) - RED_ZONE_START
                if numInRedZone > 0:
                    debug("WILL DO SOME LATER")
                    doLater = originalFileData[-numInRedZone:]
                    fileData = originalFileData[:-numInRedZone]
                    parity = parity[:-numInRedZone]
                else:
                    doLater = bytearray()
                    fileData = originalFileData

                debug("send block, size=",len(fileData))
                updiLink.ST(PTR_ITSELF, SIZE_WORD, addrToWrite )
                updiLink.picoLink.SEND_BLOCK(list(fileData),parity)

                numWritten += len(fileData)

                for dl in range(len(doLater)):
                    val = doLater[dl]
                    debug(f"STS 0x{doLater[dl]} to 0x{RED_ZONE_START+dl:x}")
                    updiLink.STS(RED_ZONE_START+dl, doLater[dl])

                chip.endFlashPage(updiLink,eraseBeforeWrite)
                debug("send_file: endPage done")

                if verify:
                    readbackData = streamRead(updiLink, len(originalFileData), targetAddress)
                    if readbackData != originalFileData:
                        ok=True
                        for e in range(len(originalFileData)):
                            if readbackData[e] != originalFileData[e]:
                                error(f"At target address 0x{targetAddress+e:x}: Data does not match: Read back 0x{readbackData[e]:02x} but expected 0x{originalFileData[e]:02x}")
                    else:
                        debug("verify: Data matches")
                        changed="[Verified]"
                else:
                    debug("Not verifying")
                    changed=""


            pct = round(100*fp.tell()/fsize)
            print(pct,"% (",fp.tell(),"of",fsize,"bytes )",changed)

    chip.finishFlashWrite(updiLink)
    endTime = time.time()
    elapsed=endTime-startTime
    ns,nr = updiLink.picoLink.STATS()

    print(f"Wrote {numWritten} bytes in {endTime-startTime:.2f} seconds = {round(numWritten*8/elapsed)} bits per second")
    print(f"With protocol overhead: Sent {ns} + Received {nr} = Total {ns+nr} = {round((ns+nr)*8/elapsed)} bits per second")

def computeParity(B:int) -> int:
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

def getStats(updiLink: UpdiLink) -> None:
    ns,nr = updiLink.picoLink.STATS()
    print("Sent:",ns,"Received:",nr,"Total:",ns+nr)


def recvFile(updiLink: UpdiLink,count: int, offset:int, fp: io.BufferedWriter) -> None:

    # ~ debug("/"*20,"recvFile","/"*20)
    #write flash using st

    # ~ debug("Begin recvFile loop")
    startTime = time.time()
    i=0
    rdata = bytearray(256)
    while i < count:
        addr = updiLink.chip.mapSectionAndGetAddress(updiLink,i+offset)
        updiLink.ST(PTR_ITSELF, SIZE_WORD, addr )
        toRead = min( [256, count-i] )
        bl: list[int] = updiLink.picoLink.RECV_BLOCK(count=toRead)

        for j in range(toRead):
            b = bl[j]
            value = b & 0xff
            parity = (b>>8) & 1
            stops = (b>>9) & 0b11
            if stops != 0b11:
                raise ProtocolError(f"Stop bits are not 11: {value:02x} {parity} {stops:02x}")
            epar = computeParity(value)
            if epar != parity:
                raise ProtocolError(f"Parity error: {value:02x} {parity} {stops:02x}")
            rdata[j] = value
        fp.write(rdata[0:toRead])
        pct = round(100*fp.tell()/count)
        print(pct,"% (",fp.tell(),"of",count,"bytes )")
        i += toRead

    endTime = time.time()
    elapsed=endTime-startTime
    print(f"Read {count} bytes in {endTime-startTime:.2f} seconds = {round(count*8/elapsed)} bits per second")

def streamRead( updiLink: UpdiLink,count:int, offset: int) -> bytearray:
    debug("streamRead: Read up to",count,"bytes starting at offset",offset)

    #bug: if we let the read get to 0xffff, the nvm controller state becomes
    #corrupted
    result = bytearray()
    while count > 0:
        tmp = streamReadOneBlock(updiLink,count,offset)
        result += tmp
        count -= len(tmp)
        offset += len(tmp)

    return result

def streamReadOneBlock(updiLink: UpdiLink,count:int, offset: int) -> bytearray:
    debug("streamReadOneBlock: Read up to",count,"bytes starting at offset",offset)



    rdata = bytearray(256)
    toRead = min( [256, count] )

    addr = updiLink.chip.mapSectionAndGetAddress(updiLink,offset)

    endAddr = addr + toRead - 1
    assert endAddr <= 0xffff,f"addr=0x{addr:x}, count={count}, endAddr=0x{endAddr:x}"
    if endAddr == 0xffff:
        doExtra=True
        toRead -= 1
    else:
        doExtra = False

    j=0

    if addr != endAddr:
        updiLink.ST(PTR_ITSELF, SIZE_WORD, addr )
        bl: list[int] = updiLink.picoLink.RECV_BLOCK(count=toRead)
        while j < toRead:
            b = bl[j]
            value = b & 0xff
            parity = (b>>8) & 1
            stops = (b>>9) & 0b11
            if stops != 0b11:
                raise ProtocolError(f"Stop bits are not 11: {value:02x} {parity} {stops:02x}")
            epar = computeParity(value)
            if epar != parity:
                raise ProtocolError(f"Parity error: {value:02x} {parity} {stops:02x}")
            rdata[j] = value
            j+=1

    if doExtra:
        rdata[j] = updiLink.LDS(endAddr)

    debug(f"streamReadOneBlock: Got {rdata[0]:02x} {rdata[1]:02x} {rdata[2]:02x} {rdata[3]:02x} {rdata[4]:02x}...")
    return rdata[:count]

#true if we know the chip has all 0xff's; used to
#optimize writing, especially for larger devices
chipHasBeenErased=False

def process_command(port:SerialPort, cmdlist: list[str]) -> None:

    global chipHasBeenErased

    #remove arguments from list as they are processed.
    #ignore extra arguments; leave them in the list
    port.connect()

    picoLink = port.picoLink
    picoLink.connect()
    chip = port.picoLink.updiLink.chip


    cmd = cmdlist.pop(0).lower()
    # ~ debug("="*20,"command:",cmd,"="*20)
    if cmd == "timeout":
        t = int(cmdlist.pop(0))
        port.picoLink.SET_TIMEOUT(t)
    elif cmd == "disconnect":
        port.picoLink.DISCONNECT_UPDI()
    elif cmd == "break":
        port.picoLink.BREAK()
    elif cmd == "data":
        sl = cmdlist.pop(0)
        ssl = sl.split(",")
        toSend = [int(q,0) for q in ssl]
        numExpected = int(cmdlist.pop(0),0)
        port.picoLink.DATA(toSend=toSend,numExpected=numExpected)
    elif cmd == "write":
        destinationOffset=0
        sourceSkip=0
        skipUnchanged=True
        eraseBeforeWrite=True
        sizeLimit=0xffffffff
        verify=True
        while True:
            filename = cmdlist.pop(0)
            if filename == ":o":
                destinationOffset = int(cmdlist.pop(0),0)
            elif filename == ":u":
                skipUnchanged = False
            elif filename == ":e":
                eraseBeforeWrite=False
            elif filename == ":z":
                sizeLimit = int(cmdlist.pop(0),0)
            elif filename == ":s":
                sourceSkip = int(cmdlist.pop(0),0)
            elif filename == ":v":
                verify=False
            else:
                break
        sendFile(port.picoLink.updiLink,filename=filename,
            #noAck=False, noAckDelay=0,
            skipUnchanged=skipUnchanged,eraseBeforeWrite=eraseBeforeWrite,
            destinationOffset=destinationOffset,sizeLimit=sizeLimit,
            sourceSkip=sourceSkip,
            chipHasBeenErased=chipHasBeenErased,
            verify=verify)
        chipHasBeenErased = False
    elif cmd == "getsib":
        sib = recvSib(port.picoLink.updiLink)
        familyID,nvmVersion0,nvmVersion1,nvmVersion2,ocdVersion0,ocdVersion1,ocdVersion2,dbgOscFreq = struct.unpack_from("7s1x3b3b1x1b", bytes(sib) )
        print("Family:",familyID)
        print("nvmVersion:",hex(nvmVersion0),hex(nvmVersion1),hex(nvmVersion2))
        print("ocdVersion:",hex(ocdVersion0),hex(ocdVersion1),hex(ocdVersion2))
        print("dbgOscFreq:",dbgOscFreq)
    elif cmd == "reset":
        port.picoLink.updiLink.requestReset()
    elif cmd == "erase":
        eraseFlash(port.picoLink.updiLink)
        chipHasBeenErased = True
    elif cmd == "geterrorstatus":
        print(bin(getErrorStatus(port.picoLink.updiLink)))
    elif cmd == "enableprogramming":
        port.picoLink.updiLink.enableNVMProgramming()
    elif cmd == "getsignature":
        print( [hex(q) for q in getSignature(port.picoLink.updiLink)] )
    elif cmd == "store":
        offset = int(cmdlist.pop(0),0)
        value = int(cmdlist.pop(0),0)
        storeToFlash(port.picoLink.updiLink,offset,value)
        if value != 0xff:
            chipHasBeenErased = False
    elif cmd == "beginflashwrite":
        beginFlashWrite(port.picoLink.updiLink)
    elif cmd == "finishflashwrite":
        chip.finishFlashWrite(port.picoLink.updiLink)
    elif cmd == "setguardtime":
        g = int(cmdlist.pop(0),0)
        setGuardTime(port.picoLink.updiLink,g)
    elif cmd == "print":
        start=int(cmdlist.pop(0),0)
        count=int(cmdlist.pop(0),0)
        v = readFlash(port.picoLink.updiLink,start,count)
        linecount=16
        for i in range(0,len(v),linecount):
            sublist: list[int] = v[i:i+linecount]
            tl: list[str] = [f"{q:02x}" for q in sublist]
            ts = " ".join(tl)
            print(f"{start+i:04x} | {ts}")
    elif cmd == "enabledelay":
        enableInterbyteDelay(port.picoLink.updiLink)
    elif cmd == "disabledelay":
        disableInterbyteDelay(port.picoLink.updiLink)
    elif cmd == "read":
        filename = cmdlist.pop(0)
        count = int(cmdlist.pop(0),0)
        offset = int(cmdlist.pop(0),0)
        with open(filename,"wb") as fp:
            recvFile(port.picoLink.updiLink,count=count,fp=fp,offset=offset)
    elif cmd == "stats":
        getStats(port.picoLink.updiLink)
    elif cmd == "updiclock":
        speed = int(cmdlist.pop(0))
        port.picoLink.updiLink.setClock(speed)
    # ~ elif cmd == "clock":
        # ~ speed = int(cmdlist.pop(0))
        # ~ chip.setClock(port.picoLink.updiLink,speed)
    elif cmd == "verify":
        fileSkip = 0
        flashOffset = 0
        while True:
            filename = cmdlist.pop(0)
            if filename == ":s":
                fileSkip = int(cmdlist.pop(0),0)
            elif filename == ":o":
                flashOffset = int(cmdlist.pop(0),0)
            else:
                break

        with open(filename,"rb") as fp:
            fp.seek(0,2)
            fileSize = fp.tell()
            fp.seek(fileSkip)
            verifySize = fileSize - fileSkip
            numOK=0
            numLeft = verifySize
            ok=True
            while numLeft > 0:
                inFlash = streamReadOneBlock(port.picoLink.updiLink,numLeft,flashOffset)
                if len(inFlash) == 0:
                    error("Error: No data returned from flash read")
                inFile = readCompletely( fp, len(inFlash), notPast=0xffffffff )

                #we limited numLeft to file size, so we should always get
                #back len(inFlash) bytes
                assert len(inFile) == len(inFlash)

                if inFile != inFlash:
                    print("Flash does not match file",filename)
                    for i in range(len(inFlash)):
                        if inFile[i] != inFlash[i]:
                            ok=False
                            print(f"Mismatch in flash at address {flashOffset+i} = 0x{flashOffset+i:04x}")
                            print(f"Expected: {inFile[i]:3d} = 0x{inFile[i]:02x} = 0b{inFile[i]:08b}")
                            print(f"In flash: {inFlash[i]:3d} = 0x{inFlash[i]:02x} = 0b{inFlash[i]:08b}")


                            # ~ print(f"NVM STATUS: {chip.getNVMControllerStatus(picoLink.updiLink):08b}")
                            # ~ chip.writeCommandToNVMController(picoLink.updiLink,0x00)
                            # ~ chip.writeCommandToNVMController(picoLink.updiLink,0x01)
                            # ~ chip.writeCommandToNVMController(picoLink.updiLink,0x00)
                            # ~ print(f"NVM STATUS: {chip.getNVMControllerStatus(picoLink.updiLink):08b}")

                            numLeft=0
                            break
                else:
                    numLeft -= len(inFlash)
                    flashOffset += len(inFlash)
                    numOK += len(inFlash)
                    print( f"{round( 100 * (numOK / verifySize))}% ({numOK} of {verifySize} bytes) OK")
            if ok:
                print("Flash matches file",filename)
    else:
        raise ValueError(f"Bad command: {cmd}")


commandHelpText="""
Commands (all are case insensitive):

    disconnect          Disconnect from UPDI
    erase               Erase all flash & EEPROM
    print s N           Print N bytes of flash starting at address s
    read F N A          Read N bytes of flash starting at offset A and
                        save to file F
    reset               Request AVR reset
    write [args] F      Write file F to flash. Optional arguments:
                          :e        Don't erase flash before writing
                          :o OFFS   Destination address (offset)
                          :s SKIP   Skip SKIP bytes from file
                          :u        Write even if flash content matches
                                    file (i.e., flash is unchanged)
                          :v        Skip verification of writes
                          :z SIZE   Write max of SIZE bytes from file
                        If :e is omitted, only the pages being written
                        are erased. If :e is given, no flash is erased.
                        If the erase command was given before
                        the write command, the chip is only erased once.
    verify [args] F     Verify flash matches file F. Optional arguments:
                          :f OFFS   Offset in flash
                          :s NUM    Skip first NUM bytes in file
"""

debugcommands = """
The following are lower-level commands useful for testing and debugging.

    beginflashwrite     Begin flash write operation
    break               Send break
    clock N             Set AVR CPU clock to N (legal values
                        depend on the chip)
    data v0,v1,...vn N    Send values v0, v1, ...and then
                        receive N bytes
    disabledelay        Disable interbyte delay
    enabledelay         Enable interbyte delay
    enableprogramming   Enable flash programming
    finishflashwrite    End flash write operation
    getsib              Get SIB signature
    geterrorstatus      Get error status from AVR
    getsignature        Get device signature
    setguardtime N      Set UPDI guard time
    stats               Show raw number of bytes sent and received
    store A V           Store value V to flash at offset A. Does not
                        erase before writing.
    timeout N           Set communication timeout
    updiclock N         Set UPDI clock to N (N=4, 8, 16, 32: MHz speed)

"""


def main() -> None:

    parser = argparse.ArgumentParser(
        epilog=commandHelpText,
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--list",action="store_true",help="List detected serial ports")
    parser.add_argument("--port",help="Name of serial port to use. Ex: COM3 or /dev/ttyACM0")
    parser.add_argument("--gpio",
        required=True,
        help="Specify GPIO for Pico. Example: --gpio 16 for the bottom right pin")
    parser.add_argument("--speed",type=int,
        action="store",help=f"Speed for communication, in bits per second; default is {int(PicoLink.DEFAULT_PIO_FREQUENCY/16)}")
    parser.add_argument("--no-enable-programming", action="store_true",help="Don't enable programming mode upon UPDI connection")
    parser.add_argument("--no-disconnect", action="store_true",help="Don't reset the AVR and disconnect when done")
    c = ", ".join(chips.getChips())
    parser.add_argument("--chip", required=True,help=f"Chip model: One of: {c}")
    parser.add_argument("commands",action="store",nargs="*",help="Commands to execute")
    parser.add_argument("--help-debug",action="store_true",help=f"Show information about debugging and lower-level commands")

    args = parser.parse_args()

    if args.list:
        SerialPort.listPorts()
        return

    chip = chips.make(args.chip)
    if chip == None:
        print(f"No such chip '{args.chip}'")
        print("Choose one of:", ", ".join(chips.getChips()))
        sys.exit(1)

    port = SerialPort(chip)

    if args.no_enable_programming:
        port.picoLink.updiLink.enableProgrammingOnConnect = False

    if args.port:
        port.setPortName(args.port)

    if args.gpio:
        gpio = int(args.gpio)
        port.picoLink.updiLink.setGPIO(gpio)

    if args.speed:
        freq = args.speed*16
        port.picoLink.updiLink.setPioFrequency(freq)

    if args.help_debug:
        print(debugcommands)
        return
    port.connect()
    port.picoLink.connect()
    try:
        i=0
        commands = args.commands[:]
        while len(commands):
            process_command(port,commands)
        sys.exit(0)
    finally:
        if not args.no_disconnect:
            debug("ONEXIT: disconnect")
            try:
                port.picoLink.updiLink.requestReset()
                port.picoLink.updiLink.disconnect()
            except Timeout:
                print("Warning: Could not disconnect on exit")


main()
