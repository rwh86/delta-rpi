#!/usr/bin/env python3

import psycopg2
import requests
import config # see config.py

#-- aggregate the high-resolution data into 5-minute time slices.  Don't overwrite existing slices in the database.
#insert into five_minute select max(date_time) as max_time, max(wh_today) as energy, round(avg(acw1)) as power, round(cast(avg(acv1) as numeric),1) as voltage, false from reading where date_time::date=current_date group by floor(extract(epoch from date_time)/60/5) having max(date_time) not in (select max_time from five_minute);
#-- the last slice won't be a complete 5-minute slice, so delete it
#DELETE FROM five_minute
#WHERE max_time IN (
#    SELECT max_time
#    FROM five_minute
#    ORDER BY max_time desc
#    LIMIT 1
#);

headers={"X-Pvoutput-Apikey":config.pvoutput["apikey"],"X-Pvoutput-SystemId":config.pvoutput["systemid"]}
db_conn = psycopg2.connect(f"dbname={config.db['dbname']} user={config.db['user']} password={config.db['password']} host={config.db['host']}")

cur = db_conn.cursor()
# aggregate the high-resolution data into 5-minute time slices.  Don't overwrite existing slices in the database.
# Note that this only works with data from today to improve performance.  If
# you need to catch up, replace current_date with the day you want to generate,
# e.g. '2019-11-09' or remove that where clause
sql = "insert into five_minute select max(date_time) as max_time, max(wh_today) as energy, round(avg(acw1)) as power, round(cast(avg(acv1) as numeric),1) as voltage, false from reading where date_time::date=current_date group by floor(extract(epoch from date_time)/60/5) having max(date_time) not in (select max_time from five_minute);"
cur.execute(sql)
db_conn.commit()

# the last slice won't be a complete 5-minute slice, so delete it
sql = """DELETE FROM five_minute
WHERE max_time IN (
    SELECT max_time
    FROM five_minute
    ORDER BY max_time desc
    LIMIT 1
);"""
cur.execute(sql)
db_conn.commit()

# get the rows that need to uploaded to pvoutput.org
sql = "select to_char(max_time,'YYYYMMDD'), to_char(max_time, 'HH24:MI'), energy, power, voltage, sent from five_minute where sent = false";
cur.execute(sql)
rows = cur.fetchall()
print(f"num rows: {len(rows)}")

#Date yyyymmdd
#Time hh:mm
#Energy Generation watt hours
#Power Generation  watts
#Energy Consumptio watt hours
#Power Consumption watts
#Temperature	    celsius
#Voltage         volts


def send_batch(data,ids_to_send):
    #curl -d 'data=20191028,06:34,0,8,,,,239.2;20191028,06:39,0,17,,,,238.0' -H "X-Pvoutput-Apikey: 841804e44aad0ef2646be8afcf56ca534d3aa417" -H "X-Pvoutput-SystemId: 71038" https://pvoutput.org/service/r2/addbatchstatus.jsp
    r = requests.post("https://pvoutput.org/service/r2/addbatchstatus.jsp", data={'data':full_csv}, headers=headers)
    # on successful upload, set sent = true in databse
    r.raise_for_status()
    if r.status_code == requests.codes.ok:
        for rid in ids_to_send:
            sql_update=f"update five_minute set sent=true where to_char(max_time,'YYYYMMDD') = '{rid[0]}' and to_char(max_time, 'HH24:MI') = '{rid[1]}'"
            print(sql_update)
            cur.execute(sql_update)
            db_conn.commit()
    else:
        print(f"status_code: {r.status_code}")

count=0
ids_to_send=[]
full_csv_i=[]
for row in rows:
    csv_i=row[0:4]+('','','')+(row[4],)
    csv_i=list(map(str,csv_i))
    csv_row=','.join(csv_i)
    #print(csv_row)
    full_csv_i.append(csv_row)
    ids_to_send.append(row[0:2])
    count+=1
    print(count)

    # send batches of 30 datapoints
    if count%30==0:
        full_csv=';'.join(full_csv_i)
        print(full_csv)
        print(ids_to_send)
        # do send to pvoutput.org
        send_batch(full_csv,ids_to_send)
        ids_to_send=[]
        full_csv_i=[]
if count%30!=0:
    print( f"remainder is: {count}" )
    # send last 0<n<30 datapoints using bulk endpoint
    full_csv=';'.join(full_csv_i)
    print(full_csv)
    send_batch(full_csv,ids_to_send)
    ids_to_send=[]
    full_csv_i=[]
