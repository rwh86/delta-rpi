#!/usr/bin/python3
# -*- coding: utf-8 -*-

import time
import binascii
import struct
import serial
import sys
import os
import signal
from argparse import ArgumentParser
from pprint import pprint
import psycopg2
import pytz
from datetime import datetime

import crc16


def ma_mi(data):
    ma, mi = struct.unpack('>BB', data)
    return '{:02d}.{:02d}'.format(ma, mi)

DEBUG=False
READ_BYTES = 1024
STX=0x02
ETX=0x03
ENQ=0x05
ACK=0x06
NAK=0x15
# Variables in the data-block of a Delta RPI M-series inverter,
# as far as I've been able to establish their meaning.
# The fields for each variable are as follows: 
# db_column, name, struct, size in bytes, decoder, multiplier-exponent (10^x), unit, SunSpec equivalent
DELTA_RPI = (
    #6: - 11 bytes part number, 502N55E0100 for an RPI H5A (string)
    (None, "SAP part number", "11s", str),
    #17 - 18 bytes serial, 241191702002503225
    (None, "SAP part number", "18s", str),
    #35 - 6 bytes unknown, 134201
    (None, "Unknown1", "6s", str),
    #41 - 2 bytes firmware revision power management, 03 28 for 3.40
    (None, "DSP FW Rev", "2s", ma_mi, 0, "MA,MI"),
    #43 - 2 bytes unknown, 10 20
    (None, "DSP FW Date", "2s", ma_mi, 0, "MA,MI"),
    #45 - 2 bytes firmware revision STS, e.g. 01 3C for version 1.60 02 00 for 2.00
    (None, "Redundant MCU FW Rev", "2s", ma_mi, 0, "MA,MI"),
    #47 - 2 bytes unknown, e.g. 0F 0C - 0d 20
    (None, "Redundant MCU FW Date", "2s", ma_mi, 0, "MA,MI"),
    #49 - 2 bytes firmware revision display, e.g. 02 24 for version 2.36 - 02 16 for 2.22
    (None, "Display MCU FW Rev", "2s", ma_mi, 0, "MA,MI"),
    #51 - 2 bytes unknown, e.g. 0F 26 - 10 32
    (None, "Display MCU FW Date", "2s", ma_mi, 0, "MA,MI"),
    #53 - 8 bytes zero
    (None, "Zero1", "8s", str),
    #61 - 09 8a = 2442
    ("acv1", "AC Voltage(Phase1)","H", float, -1, "V"),
    #63 - 03 6c = 876
    ("aca1", "AC Current(Phase1)", "H", float, -2, "A", "AphA"),
    #65 - 08 53 = 2131
    ("acw1", "AC Power(Phase1)", "H", int, 0, "W"),
    #67 - 13 8a = 5002 - inverter frequency?
    ("freq1", "AC Frequency(Phase1)", "H", float, -2, "Hz"),
    #69 - 09 88 = 2440
    ("acv2", "AC Voltage(Phase1) [Redundant]", "H", float, -1, "V"),
    #71 - 13 8c = 5004 - net frequency?
    ("freq2", "AC Frequency(Phase1) [Redundant]", "H", float, -2, "Hz"),
    #73 - 24 zeroes
    (None, "Zero2", "24s", str),
    #97 - 0a 7e = 2686
    ("dcv1", "DC Voltage(String1)", "H", float, -1, "V"),
    #99 - 01 9c = 412
    ("dca1", "DC Current(String1)", "H", float, -2, "A"),
    #101 - 04 56 = 1110
    ("dcw1", "DC Power(String1)", "H", int, 0, "W"),
    #103 - 0a 5b = 2651
    ("dcv2", "DC Voltage(String2)", "H", float, -1, "V"),
    #105 - 01 9b = 411
    ("dca2", "DC Current(String2)", "H", float, -2, "A"),
    #107 - 04 47 = 1095
    ("dcw2", "DC Power(String2)", "H", int, 0, "W"),
    #109 - 08 53 = 2131
    ("acw2", "AC Power(Phase1) [Redundant]", "H", int, 0, "W"),
    #111 - 4 bytes zero
    (None, "Zero3", "4s", str),
    #115 - 00 00 27 10 = 10000 - energy so far today?
    ("wh_today", "Supplied AC energy today", "I", int, 0, "Wh"),
    #119 - 00 00 5f 2a = 24362 - seconds runtime?
    ("time_today", "Inverter runtime today", "I", int, 0, "s"),
    #123 - 00 00 33 86 = 13190
    ("kwh_total", "Supplied AC energy (lifetime)", "I", int, 0, "Wh"),
    #127 - 00 00 86 02 = 34306
    ("time_total", "Inverter runtime (lifetime)", "I", int, 0, "s"),
    #131 - b7    = 183
    (None, "Zero4", "38s", str)
)

DELTA_RPI_STRUCT = '>' + ''.join([item[2] for item in DELTA_RPI])
DUMMY_DATA_RAW = b'3530324e353545303130303234313139313730323030323530333232353133343230310328102002000d20021610320000000000000000096f000200001387096a138900000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000337c0218068d0000000000000000000000000000000000000000000000000000000000000000000000000000'
DUMMY_DATA = (
    b'802FA0E1000',  # SAP part number
    b'O1S16300040WH',  # SAP serial number
    b'0901',  # SAP date code
    b'0\x00',  # SAP revision
    b'\x01#',  # DSP FW Rev
    b'\x0f0',  # DSP FW Date
    b'\x01\r',  # Redundant MCU FW Rev
    b'\x0f\x0e',  # Redundant MCU FW Date
    b'\x01\x10',  # Display MCU FW Rev
    b'\x0f0',  # Display MCU FW Date
    b'\x00\x00',  # Display WebPage Ctrl FW Rev
    b'\x00\x00',  # Display WebPage Ctrl FW Date
    b'\x00\x00',  # Display WiFi Ctrl FW Rev
    b'\x00\x00',  # Display WiFi Ctrl FW Date
    0,  # AC Voltage(Phase1)
    0,  # AC Current(Phase1)
    0,  # AC Power(Phase1)
    0,  # AC Frequency(Phase1)
    0,  # AC Voltage(Phase1) [Redundant]
    0,  # AC Frequency(Phase1) [Redundant]
    0,  # AC Voltage(Phase2)
    0,  # AC Current(Phase2)
    0,  # AC Power(Phase2)
    0,  # AC Frequency(Phase2)
    0,  # AC Voltage(Phase2) [Redundant]
    0,  # AC Frequency(Phase2) [Redundant]
    0,  # AC Voltage(Phase3)
    0,  # AC Current(Phase3)
    0,  # AC Power(Phase3)
    0,  # AC Frequency(Phase3)
    0,  # AC Voltage(Phase3) [Redundant]
    0,  # AC Frequency(Phase3) [Redundant]
    0,  # Solar Voltage at Input 1
    0,  # Solar Current at Input 1
    0,  # Solar Power at Input 1
    0,  # Solar Voltage at Input 2
    0,  # Solar Current at Input 2
    0,  # Solar Power at Input 2
    0,  # ACPower
    0,  # (+) Bus Voltage
    0,  # (-) Bus Voltage
    0,  # Supplied ac energy today
    0,  # Inverter runtime today
    0,  # Supplied ac energy (total)
    0,  # Inverter runtime (total)
    0,  # Calculated temperature inside rack
    0,  # Status AC Output 1
    0,  # Status AC Output 2
    0,  # Status AC Output 3
    0,  # Status AC Output 4
    0,  # Status DC Input 1
    0,  # Status DC Input 2
    0,  # Error Status
    0,  # Error Status AC 1
    0,  # Global Error 1
    0,  # CPU Error
    0,  # Global Error 2
    0,  # Limits AC output 1
    0,  # Limits AC output 2
    0,  # Global Error 3
    0,  # Limits DC 1
    0,  # Limits DC 2
    b'\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00',  # History status messages
)


def signal_handler(signal, frame):
    ''' Catch SIGINT/SIGTERM/SIGKILL and exit gracefully '''
    print("Stop requested...")
    sys.exit(0)
signal.signal(signal.SIGINT, signal_handler)
signal.signal(signal.SIGTERM, signal_handler)


def send(conn, req, cmd, subcmd, data=b'', addr=1):
    """
    Send cmd/subcmd (e.g. 0x60/0x01) and optional data to the RS485 bus
    """
    assert req in (ENQ, ACK, NAK)  # req should be one of ENQ, ACK, NAK
    msg = struct.pack('BBBBB', req, addr, 2 + len(data), cmd, subcmd)
    if len(data) > 0:
        msg = struct.pack('5s%ds' % len(data), msg, data)
    crcval = crc16.calcData(msg)
    lsb = crcval & (0xff)
    msb = (crcval >> 8) & 0xff
    data = struct.pack('B%dsBBB' % len(msg), STX, msg, lsb, msb, ETX)
    if DEBUG: print(">>> SEND:", binascii.hexlify(msg), "=>", binascii.hexlify(data))
    conn.write(data)
    conn.flush()


def receive(conn):
    """ 
    Attempt to read messages from a serial connection
    """
    data = bytearray()
    while True:
        buf = conn.read(READ_BYTES)
        if buf:
            if DEBUG: print(">>> RAW RECEIVE:", buf)
            data.extend(buf)
        if (not buf) or len(buf) < READ_BYTES:
            break

    idx = 0
    while idx + 9 <= len(data):
        if data[idx] != STX:
            idx += 1
            continue
        stx, req, addr, size = struct.unpack('>BBBB', data[idx:idx+4])
        if req not in (ENQ, ACK, NAK):
            print("Bad req value: {:02x} (should be one of ENQ/ACK/NAK)".format(req))
            idx += 1
            continue
        if idx + 4 + size >= len(data):
            print("Can't read %d bytes from buffer" % size)
            idx += 1
            continue
        msg, lsb, msb, etx = struct.unpack('>%dsBBB' % size, data[idx+4:idx+7+size])
        if etx != ETX:
            print("Bad ETX value: {:02x}".format(etx))
            idx += 1
            continue
        crc_calc = crc16.calcData(data[idx+1:idx+4+size])
        crc_msg = msb << 8 | lsb
        if crc_calc != crc_msg:
            print("Bad CRC check: %s <> %s" % (binascii.hexlify(crc_calc), binascii.hexlify(crc_msg)))
            idx += 1
            continue

        if DEBUG: print(">>> RECV:", binascii.hexlify(data), "=>", binascii.hexlify(msg))
        yield {
            "stx": stx,
            "req": req,
            "addr": addr,
            "size": size,
            "msg": msg,
            "lsb": lsb,
            "msb": msb,
            "etx": etx,
        }
        idx += 4 + size
            

def decode_msg(data):
    req = data['req']
    cmd, cmdsub = struct.unpack('>BB', data['msg'][0:2])
    data['cmd'] = cmd
    data['cmdsub'] = cmdsub
    data['raw'] = data['msg'][2:]
    if req == NAK:
        print("NAK value received: cmd/subcmd request was invalid".format(req))
    elif req == ENQ:
        if DEBUG: print("ENQ value received: request from master (datalogger)")
    elif req == ACK:
        if DEBUG: print("ACK value received: response from slave (inverter)")
        data['values'] = struct.unpack(DELTA_RPI_STRUCT, data['raw'])
    if DEBUG: pprint(data)
    return data


def main():
    global DEBUG, MODE
    parser = ArgumentParser(description='Delta inverter simulator (slave mode) or datalogger (master mode) for RPI H5A')
    parser.add_argument('-a', metavar='ADDRESS', type=int,
                      default=1,
                      help='slave address [default: 1]')
    parser.add_argument('-d', metavar='DEVICE',
                      default='/dev/ttyUSB0',
                      help='serial device port [default: /dev/ttyUSB0]')
    parser.add_argument('-b', metavar='BAUDRATE',
                      default=19200,
                      help='baud rate [default: 19200]')
    parser.add_argument('-t', metavar='TIMEOUT', type=float,
                      default=2.0,
                      help='timeout, in seconds (can be fractional, such as 1.5) [default: 2.0]')
    parser.add_argument('--debug', action="store_true",
                      help='show debug information')
    parser.add_argument('mode', metavar='MODE', choices=['master', 'slave'],
                      help='mode can either be "master" or "slave"')
    parser.add_argument('--db', action="store_true",
                      help='write output to the database rather than echoing to the console')

    args = parser.parse_args()
    DEBUG = args.debug
    MODE = args.mode
    DB = False
    DB = args.db

    if DB:
        db_conn = psycopg2.connect("dbname=delta-rpi user=delta-rpi password=fXEAXq94uKeLi6 host=localhost")
        cur = db_conn.cursor()

    conn = serial.Serial(args.d, args.b, timeout=args.t);
    conn.flushOutput()
    conn.flushInput()
    while True:
        if MODE == 'master':
            send(conn, ENQ, 0x60, 0x01, addr=args.a)
            time.sleep(0.1)
        for data in receive(conn):
            if MODE == 'master' and data['addr'] == args.a and data['req'] in (ACK, NAK,):
                d = decode_msg(data)
                if d['req'] == ACK:
                    if not (d['cmd'] == 0x60 and d['cmdsub'] == 0x01):
                        print("Can't decode request cmd=0x%02X, cmdsub=0x%02X" % (d['cmd'], d['cmdsub']))
                        print("The only supported request is cmd=0x60, cmdsub=0x01")
                        continue

                    if DB:
                        db_cols=['rs485id','date_time']
                        now = datetime.now(pytz.utc)
                        dt_str = now.strftime("%Y-%m-%d %H:%M:%S%z")
                        vals=[1,f"'{dt_str}'"]
                        commit=True
                    else:
                        print(61 * '=')
                    for i, item in enumerate(DELTA_RPI):
                        db_col = item[0]
                        label = item[1]
                        decoder = item[3]
                        scale = item[4] if len(item) > 4 else 0
                        units = item[5] if len(item) > 5 else ''
                        value = decoder(data['values'][i])
                        if decoder == float:
                            value = value * pow(10, scale)
                        if DB:
                            if db_col is not None:
                                db_cols.append(db_col)
                                vals.append(value)
                            # don't commit data to the db if we're not generating
                            if db_col=='dcv1' and value == 0.0:
                                commit=False
                                if DEBUG:
                                    print("No AC voltage: skipping DB commit")
                        else:
                            print('%-40s %20s %-10s' % (label, value, units))
                    if DB:
                        db_col_sql=','.join(db_cols)
                        val_sql=','.join(map(str,vals))
                        sql=f"insert into reading ({db_col_sql}) values ({val_sql});"
                        if commit:
                            print(f"COMMIT IS: {str(commit)}")
                            cur.execute(sql)
                            db_conn.commit()
                            if DEBUG:
                                print(sql)
            if MODE == 'slave' and data['addr'] == args.a and data['req'] in (ENQ,):
                d = decode_msg(data)
                if d['cmd'] == 0x60 and d['cmdsub'] == 0x01:
                    if DUMMY_DATA_RAW is not None:
                        raw = DUMMY_DATA_RAW
                    else:
                        raw = struct.pack(DELTA_RPI_STRUCT, *DUMMY_DATA)
                    send(conn, ACK, 0x60, 0x01, data=raw, addr=args.a)
                else:
                    print("This simulator only replies to cmd=0x60 cmdsub=0x01 requests...")


if __name__ == "__main__":
    main()
