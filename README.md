# Delta RPI H5A RS485 simulator/dataviewer

Based on https://github.com/bbinet/delta-rpi, converted to parse data from the Delta RPI H5A_020 instead of the Delta RPI M8A.  I added the capability to log to a PostgreSQL database, aggregate 5-minute timeslices and upload to pvoutput.org.

I bought a USB rs485 serial adapter from ebay, plugged it into my pi3, and it came up as /dev/ttyUSB0, just with the default kernel driver. This is the one I got, but most should just work these days: https://www.ebay.com.au/itm/USB-RS485-USB-485-Converter-Adapter-Support-For-Win-Vista-For-Linux-For-Mac-Q3/153508192742

This repo gives a really good description of what's going on with the RS485 serial protocol:

https://github.com/lvzon/soliviamonitor

This repo gave info on connecting the RS485 adapter and uploading data to pvoutput.org, although I ended up writing my own code for this:

https://github.com/runesl/DeltaPVOutput

RS485 is a broadcast serial bus. You put a request onto the bus with a set of specific bytes which includes an address (which inverter ID you're talking to) and a command (what you want the inverter to send back). The inverter then replies with a bunch of bytes which are effectively a serialised C struct (a fancy way of saying a bunch of stuff all jammed together, represented as a stream of bytes).

The repos I linked above have two things: a little shell script that you run to put the serial device into the correct mode so that it doesn't garble the data, and then a script which will send the command, get the resulting bytes back, then unpack them, pulling off the header, checking the payload against the CRC (parity check) and then mapping the bytes to certain values. The tricky bit is working out what the bytes that are coming back actually are. It seems that different models have different structures.

What I did was to use the https://github.com/bbinet/delta-rpi/delta-rpi.py script with the --debug switch. This does all the bits to pull out the payload and then dumps it out as text. I then stripped out a whole days worth of that, loaded it into a text editor, and visually looked at which bytes were changing. The bytes that stayed the same were static info (like inverter model number, firmware version, manufacture date etc) and the bytes that were changing were the actual intersting data.

Then it's a matter of looking at them and determining whether they're 2-byte or 4-byte numbers, and then looking at the values to work out what they're likely to be. For example. If it's always 500, or 499 or 501, then it's the AC line frequency (needs to be divided by 10). If it starts off small, like 4 then 10 then 15 and ends up around 4000, then it's the AC Power in watts. If it's a huge number like 150000 and it increments slowly throughout the day and ends up around 150025, then it's the lifetime power generation (kWh). Mine had two columns that incremented by the amount of seconds the inverter had been feeding back to the grid (today's feed-in time) and total of feed-in time (lifetime feed-in time).

Then I wrote a little script which took my assumptions about the data and dumped out a CSV based on that, so I could look at the values in a spreadsheet to see if they made sense.

Anyway, most of the code I used is from the repos I linked above. I'll knock my own scripts into shape over the next few days and will post back when they're done.

Thanks to the https://github.com/lvzon/soliviamonitor/ project which has
provided the crc checking code and a little shell-script that will put a device
into raw-mode and will undefine all special control characters on the specified
device, e.g.: `./unset_serial_ctrlchars.sh /dev/ttyRPC0`

## Usage

The `delta-rpi.py` script should be run with Python3 only (as the
script is not compatible with Python2).

The script can either work as an Delta RPI M8A RS485 simulator (slave mode) or
as a simple dataviewer (master mode).

See the usage message below:

```
    $ python3 delta-rpi.py -h
    usage: delta-rpi.py [-h] [-a ADDRESS] [-d DEVICE] [-b BAUDRATE]
                                      [-t TIMEOUT] [--debug]
                                      MODE
    
    Delta inverter simulator (slave mode) or dataviewer (master mode) for RPI M8A
    
    positional arguments:
      MODE         mode can either be "master" or "slave"
    
    optional arguments:
      -h, --help   show this help message and exit
      -a ADDRESS   slave address [default: 1]
      -d DEVICE    serial device port [default: /dev/ttyUSB0]
      -b BAUDRATE  baud rate [default: 19200]
      -t TIMEOUT   timeout, in seconds (can be fractional, such as 1.5) [default:
                   2.0]
      --debug      show debug information
```

So to simulate an inverter on a RaspberryPi with Raspicomm RS485 adapter
(with rs485 address=1, serial port=/dev/ttyRPC0, baud rate=19200), you can run:

```
    $ python3 delta-rpi.py -d /dev/ttyRPC0 -b 19200 -a 1 slave
```

And to act as a dataviewer and retrieve data from an inverter on a RaspberryPi
with Raspicomm RS485 adapter (with rs485 address=1, serial port=/dev/ttyRPC0,
baud rate=19200), you can run:

```
    $ python3 delta-rpi.py -d /dev/ttyRPC0 -b 19200 -a 1 master
```

## Testing both slave and master modes

You can easily test the simulator by using virtual serial ports that you can
create using socat:

```
    $ socat -d -d pty,raw,echo=0 pty,raw,echo=0
    2017/07/05 14:27:51 socat[12075] N PTY is /dev/pts/2
    2017/07/05 14:27:51 socat[12075] N PTY is /dev/pts/3
    2017/07/05 14:27:51 socat[12075] N starting data transfer loop with FDs [3,3] and [5,5]
```

You can now use the `/dev/pts/2` and `/dev/pts/3` virtual serial ports to run
the `delta-rpi.py` script in both master and slave mode.

Run the script in slave mode (inverter simulator) in a first terminal:
```
    $ python3 delta-rpi.py -d /dev/pts/2 -b 19200 -a 1 slave
```

Run the script in master mode in a second terminal:
```
    $ python3 delta-rpi.py -d /dev/pts/3 -b 19200 -a 1 master
```

You should now see the slave and master sending/receiving dummy data.


#Database creation

```
sudo -u postgres createuser delta-rpi -P
sudo -u postgres createdb delta-rpi -O delta-rpi

CREATE TABLE reading(
	id	serial primary key,
	date_time	timestamp with time zone,
	rs485id smallint,
	acv1	real,
	aca1	real,
	acw1	smallint,
	freq1	real,
	acv2	real,
	freq2	real,
	dcv1	real,
	dca1	real,
	dcw1	smallint,
	dcv2	real,
	dca2	real,
	dcw2	smallint,
	acw2	smallint,
	wh_today	integer,
	time_today	integer,
	kwh_total	integer,
	time_total	integer
);

create index "date_time_idx" on reading("date_time");

# aggregate to 5-minute intervals
create table five-minute(
	date     text,
	time     text,
	energy   integer,
	power    smallint,
	sent     boolean
)
#select to_char(max(date_time) at time zone 'utc', 'YYYY-MM-DD') as date, to_char(max(date_time) at time zone 'utc', 'HH24:MI:SS') as time, round(avg(acw1)) as avg_acw1 from reading group by floor(extract(epoch from date_time)/60/5) order by date, time asc;
#v1	Energy Generation	No1	number	watt hours	10000	r1	
#v2	Power Generation	No	number	watts	2000	r1	
	select to_char(max(date_time), 'YYYY-MM-DD') as date, to_char(max(date_time), 'HH24:MI:SS') as time, max(wh_today) as energy, round(avg(acw1)) as power, round(cast(avg(acv1) as numeric),1) as voltage from reading group by floor(extract(epoch from date_time)/60/5) order by date, time asc;
	select to_char(max(date_time), 'YYYY-MM-DD') as date, to_char(max(date_time), 'HH24:MI:SS') as time, max(wh_today) as energy, round(avg(acw1)) as power, round(cast(avg(acv1) as numeric),1) as voltage from reading group by floor(extract(epoch from date_time)/60/5) order by date, time asc;

create table five_minute(
	max_time	timestamp with time zone,
	energy   integer,
	power    smallint,
	voltage  real,
	sent     boolean
);

-- aggregate the high-resolution data into 5-minute time slices.  Don't overwrite existing slices in the database.
insert into five_minute select max(date_time) as max_time, max(wh_today) as energy, round(avg(acw1)) as power, round(cast(avg(acv1) as numeric),1) as voltage, false from reading where date_time::date=current_date group by floor(extract(epoch from date_time)/60/5) having max(date_time) not in (select max_time from five_minute);
-- the last slice won't be a complete 5-minute slice, so delete it
DELETE FROM five_minute
WHERE max_time IN (
    SELECT max_time
    FROM five_minute
    ORDER BY max_time desc
    LIMIT 1
);
```