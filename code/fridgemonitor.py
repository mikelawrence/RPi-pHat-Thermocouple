#!/usr/bin/python3
# -*- coding: utf-8 -*-
#
# Reads temperatures from Raspberry Pi Thermocouple pHat by Mike Lawrence.
# Takes temperature averages and publishes to MQTT server.
# Supports Home Assistant MQTT Discovery directly.
#
# The MIT License (MIT)
# 
# Copyright (c) 2019 Mike Lawrence
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in
# all copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN
# THE SOFTWARE.

# Can enable debug output by uncommenting:
import logging
logging.basicConfig(format='Fridge Monitor: %(message)s', level=logging.DEBUG)

import threading
import os
import sys
import signal
import configparser
import json
import time
import datetime
from subprocess import PIPE, Popen

from w1thermsensor import W1ThermSensor
from w1thermsensor import NoSensorFoundError
from w1thermsensor import SensorNotReadyError
import paho.mqtt.client as mqtt
import RPi.GPIO as GPIO

from timer import InfiniteTimer
from tempdata import TempData

# User configurable values
ENABLE_AVAILABILITY_TOPIC = True
FIRMWARE = "0.1.0"
CONFFILE = "fridgemonitor.conf"
STATEFILE = "fridgemonitor.json"
ALERT = 27                      # Pin number of alert signal on PCB
SAVEFILEFREQ = 60               # how long to delay writing to state file
QOS = 1                         # MQTT Quality of Service

# Global variables
Mqttc = None
Mqttconnected = False
Lock = threading.Lock()
SaveStateTimer = None
StartTime = None
ResetAlarmDisable = True        # when True and hour is 6PM reset Alarm Disable

class GracefulKiller:
    """Class to handle SIGTERM signal."""
    kill_now = False
    def __init__(self):
        signal.signal(signal.SIGINT, self.exit_gracefully)
        signal.signal(signal.SIGTERM, self.exit_gracefully)

    def exit_gracefully(self,signum, frame):
        self.kill_now = True

def saveStateFile():
    """Save state to file."""
    global SaveState
    with open(STATEFILE, 'w') as outfile:
        json.dump(SaveState, outfile)
    logging.info("Updated current state file %s.", STATEFILE)

def queueSaveStateFile(state):
    """Queue save state to file while preventing frequent writes to Flash."""
    global SaveState, SaveStateTimer
    # try to cancel existing timer
    try:
        SaveStateTimer.cancel()
    except:
        pass
    # keep track of state to save to file
    SaveState = state
    # delay executing save to state file function
    SaveStateTimer = threading.Timer(SAVEFILEFREQ, saveStateFile)
    SaveStateTimer.start()

def buzzer_on():
    """Turns ON alert buzzer on Thermocouple Hat."""
    GPIO.output(ALERT, GPIO.HIGH)

def buzzer_off():
    """Turns OFF alert buzzer on Thermocouple Hat."""
    GPIO.output(ALERT, GPIO.LOW)

def buzzer_beep(beeptime):
    """Turns ON alert buzzer on Hat for a period of time in seconds.
    
        :time: Time in seconds to beep (float).
    """
    GPIO.output(ALERT, GPIO.HIGH)
    time.sleep(beeptime)
    GPIO.output(ALERT, GPIO.LOW)

def get_max31850k_address(sensor):
    """Returns the address of the MAX31850 1-Wire determined by the 
        AD0-AD3 inputs on the device.

        :returns: The address with a range of 0-15

        :raises NoSensorFoundError: if the sensor could not be found or it 
                                    is not a MAX31850.
        :raises SensorNotReadyError: if the sensor is not ready yet.
    """
    # throw error if not a MAX31850 1-wire
    if sensor.type != 0x3b:
        raise NoSensorFoundError(sensor.type, sensor.id)
    # reading temperature will read the entire scratchpad
    try:
        with open(sensor.sensorpath, "r") as f:
            data = f.readlines()
    except IOError:
        raise NoSensorFoundError(sensor.type, sensor.id)
    # make sure sensor is ready
    if data[0].strip()[-3:] != "YES":
        raise SensorNotReadyError()
    dataASCIIBytes = data[0].split(" ")
    # time to finally get the address
    return int(dataASCIIBytes[4], 16) & 0x0F

def measureSensors():
    """Take temperature and WiFi RSSI measurements.
        Executed in a thread different from main thread.
    """
    global StartTime, Changed, CurState, NextState
    # initialize StartTime
    if not StartTime:
        StartTime = time.time()
    # sample the temperatures (upwards of 1 sec per sensor)
    for i in range(0, TC_Count + 1):
        Temps[i].append(TC[i].get_temperature(W1ThermSensor.DEGREES_C))
    # determine if it is time to publish sensors
    if (time.time() - StartTime > 
        Config['Temperature Sensors'].getint('Sensor_Publish_Rate')):
        # update StartTime to next interval
        StartTime += Config['Temperature Sensors'].getint(
            'Sensor_Publish_Rate')
        # publish temps
        for i in range(0, TC_Count + 1):
            Mqttc.publish(ConfigTemp[i]['stat_t'], 
                '{:0.2f}'.format(Temps[i].average()), qos=QOS, retain=True)
        # publish RSSI but first get from iwconfig
        process = Popen(['iwconfig', 'wlan0'], stdout=PIPE)
        output, _error = process.communicate()
        rssi = -1000
        for line in output.decode("utf-8").split("\n"):
            if "Signal level" in line:
                rssi = int(line.split("Signal level=")[1].split(" ")[0])
        if (rssi > -1000):
            # RSSI was measured, time to publish
            Mqttc.publish(ConfigRSSI['stat_t'], str(rssi), qos=QOS,
                retain=True)
        # needs to be thread safe
        with Lock:
            # check for alarm conditions (only on TC's)
            for i in range(1, TC_Count + 1):
                if CurState['alarms'][i] == True:
                    # alarm in progress
                    if (Temps[i].average() <= 
                        Config['Temperature Sensors'].getfloat(
                            'TC1_Alarm_Reset_Temp')):
                        NextState['alarms'][i] = False
                else:
                    # alarm not in progress
                    if (Temps[i].average() >= 
                        Config['Temperature Sensors'].getfloat(
                            'TC1_Alarm_Set_Temp')):
                        NextState['alarms'][i] = True
            # logical or of all alarms (only on TC's)
            NextState['alarm'] = False
            for i in range(1, TC_Count + 1):
                if NextState['alarms'][i] == True:
                    NextState['alarm'] = True
            # determine alarm change and update alarm status
            if CurState['alarm'] !=  NextState['alarm']:
                Changed = True
        # Clear temperature averages
        for i in range(0, TC_Count + 1):
            Temps[i].clear()

def mqtt_on_message(mqttc, obj, msg):
    """Handle MQTT message events.
        Executed in a thread different from main thread.
    """
    global Changed, CurState, NextState
    if (msg.topic == ConfigAlarmDisable['cmd_t']):
        # received an Alarm Disable command
        payload = msg.payload.decode("utf-8").strip()
        # needs to be thread safe
        with Lock:        
            if payload.lower() == 'on':
                NextState['alarm_disable'] = True
            elif payload.lower() == 'off':
                NextState['alarm_disable'] = False
            else:
                logging.warning("Warning, unknown Alarm Disable " +
                    "command payload '%s'.",payload)
            if CurState['alarm_disable'] != NextState['alarm_disable']:
                Changed = True
    else:
        logging.warning("Warning, unknown command topic '%s', " +
            "with payload '%s'.", msg.topic, msg.payload.decode("utf-8"))

def mqtt_on_connect(mqttc, userdata, flags, rc):
    """Handle MQTT connection events.
        Executed in a thread different from main thread.
    """
    global Changed, Mqttconnected
    if rc == 0:
        # connection was successful
        logging.info("Connected to MQTT broker: mqtt://%s:%s.",
            mqttc._host, mqttc._port)
        # publish node configs if discovery is on
        if Config.getboolean('Home Assistant', 'Discovery_Enabled'):
            # discovery is enabled so publish config data
            mqttc.publish("/".join([TopicAlarmDisable, 'config']),
                payload=json.dumps(ConfigAlarmDisable), qos=QOS, retain=True)
            mqttc.publish("/".join([TopicAlarm, 'config']),
                payload=json.dumps(ConfigAlarm), qos=QOS, retain=True)
            mqttc.publish("/".join([TopicRSSI, 'config']),
                payload=json.dumps(ConfigRSSI), qos=QOS, retain=True)
            for i in range(0, 4):
                if i <= TC_Count:
                    # set defined temperature config topics
                    mqttc.publish("/".join([TopicTemp[i], 'config']),
                        payload=json.dumps(ConfigTemp[i]), qos=QOS, 
                        retain=True)
                else:
                    # clear undefined temperature config topics
                    mqttc.publish("/".join([TopicTemp[i], 'config']),
                        payload="", qos=QOS, retain=False)
        else:
            # discovery is disabled so clear temperature config topics
            mqttc.publish("/".join([TopicAlarmDisable, 'config']),
                payload="", qos=QOS, retain=False)
            mqttc.publish("/".join([TopicRSSI, 'config']),
                payload="", qos=QOS, retain=False)
            for i in range(0, 4):
                mqttc.publish("/".join([TopicTemp[i], 'config']),
                    payload="", qos=QOS, retain=False)
        # subscribe to Alarm Disable Switch command topic
        mqttc.subscribe(ConfigAlarmDisable['cmd_t'])
        # update availability
        mqttc.publish(TopicAvailability, payload=PayloadAvailable, 
            qos=QOS, retain=True)
        # indicate we are now connected
        Mqttconnected = True
        # force update of states
        Changed = True
    else:
        # connection failed
        if rc == 5:
            # MQTT Authentication failed
            print("MQTT authentication failed: " +
                "mqtt://%s:%s".format(mqttc._host, mqttc._port))
        else:
            print("MQTT_ERR=%s: Failed to connect to broker: mqtt://%s:%s.",
                rc, mqttc._host, mqttc._port)

def mqtt_on_disconnect(client, userdata, rc):
    """Handle MQTT disconnect events.
        Executed in a thread different from main thread.
    """
    global Mqttconnected
    Mqttconnected = False

def mqtt_subscribe():
    """Not used.
        Executed in a thread different from main thread.
    """
    pass

# Main program starts here
try:
    # verify the pHat exists
    if not os.path.isdir("/proc/device-tree/hat"):
        logging.error("Error, No Hat detected.")
        sys.exit()     

    # get hat information
    with open("/proc/device-tree/hat/product", 'r') as f:
        hat_product = f.readline(100).rstrip('\x00')
    with open("/proc/device-tree/hat/vendor", 'r') as f:
        hat_vendor = f.readline(100).rstrip('\x00')
    with open("/proc/device-tree/hat/product_id", 'r') as f:
        hat_productid = f.readline(100).rstrip('\x00')
    with open("/proc/device-tree/hat/product_ver", 'r') as f:
        hat_version = f.readline(100).rstrip('\x00')
        hat_version = int(hat_version, 16)
    with open("/proc/device-tree/hat/uuid", 'r') as f:
        hat_uuid = f.read()

    # hat is present make sure it is the right one
    if not hat_product == "Raspberry Pi Thermocouple pHat":
        logging.error("Error, Found incorrect Hat: %s.", hat_product)
        sys.exit()     
    if not hat_vendor == "Mike Lawrence":
        logging.error("Error, Found incorrect Hat: %s.", hat_product)
        sys.exit()     
        
    # we found a Raspberry Pi Thermocouple pHat
    logging.info("Found Raspberry Pi Thermocouple pHat, Rev %s.%s.", 
        hat_version//256, hat_version%256)

    # initialize board
    GPIO.setwarnings(False)
    GPIO.setmode(GPIO.BCM)
    GPIO.setup(ALERT, GPIO.OUT)
    GPIO.output(ALERT, GPIO.LOW)

    # load config file
    if not os.path.isfile(CONFFILE):
        logging.error("Config file is missing %s.", hat_product)
        sys.exit()
    Config = configparser.ConfigParser(
        defaults = {
            'Broker': '127.0.0.1',
            'Port': '1883',
            'KeepAlive': '60',
            'UserName': '',
            'Password': '',
            'Discovery_Enabled': 'false',
            'Discovery_Prefix': 'homeassistant',
            'Node_ID': 'fridge_monitor',
            'Node_Name': 'Fridge Monitor',
            'TC_Count': '3',
            'TC1_Name': 'TC1 Temperature',
            'TC1_Alarm_Set_Temp': '15',
            'TC1_Alarm_Reset_Temp': '10',
            'TC2_Name': 'TC2 Temperature',
            'TC2_Alarm_Set_Temp': '15',
            'TC2_Alarm_Reset_Temp': '10',
            'TC3_Name': 'TC3 Temperature',
            'TC3_Alarm_Set_Temp': '15',
            'TC3_Alarm_Reset_Temp': '10',
            'Sensor_Publish_Rate': '60',
        })
    Config.read(CONFFILE)

    # check TC_Count Range
    TC_Count = Config['Temperature Sensors'].getint('TC_Count')
    if TC_Count < 1:
        TC_Count = 1
        logging.info("Config file 'TC_Count' less than 1. Set to 1.")
    if TC_Count > 3:
        TC_Count = 3
        logging.warning("Warning, config file 'TC_Count' greater than 3. " +
            "Set to 3.")
    # update TC_Count in Config
    Config['Temperature Sensors']['TC_Count'] = str(TC_Count)
    # check Sensor Publish Rate Range
    Sensor_Publish_Rate = Config['Temperature Sensors'].getint(
        'Sensor_Publish_Rate')
    if Sensor_Publish_Rate < 60:
        Sensor_Publish_Rate = 60
        logging.warning("Warning, config file 'Sensor_Publish_Rate' less " + 
            "than 60 seconds. Set to 60.")
    Config['Temperature Sensors']['Sensor_Publish_Rate'] = str(
        Sensor_Publish_Rate)

    # find MAX31850k Thermocouple Digitizers on board
    sensors = W1ThermSensor.get_available_sensors(
        [W1ThermSensor.THERM_SENSOR_MAX31850K])
    # make sure we detected enough MAX31850K's
    if len(sensors) < TC_Count:
        logging.error("Error, Found %s MAX31850Ks, expected %s.",
            len(sensors), TC_Count)
        sys.exit()
    TC = [None] * (TC_Count + 1)
    # get and sort sensors by address
    for sensor in sensors:
        address = get_max31850k_address(sensor) + 1
        if address <= TC_Count:
            TC[address] = sensor
    # find DS18S20 sensor on PCB
    try:
        TC[0] = W1ThermSensor.get_available_sensors(
            [W1ThermSensor.THERM_SENSOR_DS18S20])[0]
        logging.info("DS18S20 Board Sensor is ID=%s.", TC[0].id)
    except NoSensorFoundError:
        logging.error("Error, failed to discover board DS18S20.")
        sys.exit()
    # log sensor information
    logging.info("TC1 is '%s' is ID=%s.",
        Config['Temperature Sensors']['TC1_Name'], TC[1].id)
    if TC_Count > 1:        
        logging.info("TC2 is '%s' is ID=%s.",
            Config['Temperature Sensors']['TC2_Name'], TC[2].id)
    if TC_Count > 2:        
        logging.info("TC3 is '%s' is ID=%s.",
            Config['Temperature Sensors']['TC3_Name'], TC[3].id)

    # create TempData arrays
    Temps = [None] * (TC_Count + 1)
    for i in range(0, TC_Count + 1):
        if i == 0:
            Temps[0] = TempData(0, "Board Sensor")
        if i == 1:
            Temps[1] = TempData(0, Config['Temperature Sensors']['TC1_Name'])
        if i == 2:
            Temps[2] = TempData(0, Config['Temperature Sensors']['TC2_Name'])
        if i == 3:
            Temps[3] = TempData(0, Config['Temperature Sensors']['TC3_Name'])


    # load current state file
    try:
        with open(STATEFILE, 'r') as infile:
            CurState = json.load(infile)
        logging.info("Loaded state file %s.", STATEFILE)
    except:
        # load defaults if there is an exception in loading the state file
        logging.warning("Warning, failed to load state file. Defaults loaded.", 
            STATEFILE)
        CurState = {
            'alarm_disable': False,
            'alarms': [False, False, False, False],
            'alarm': False,
        }
        
    # alarms default to off on first run, so really there is no need to save 
    #   them to disk but it's easier to save them with the alarm disable state
    CurState['alarms'] = [False, False, False, False]
    CurState['alarm'] = False
    # on startup NextState is the same as CurState
    NextState = CurState.copy()
    # since this is a first run when should update the MQTT server states
    Changed = True

    # generate availability strings
    TopicAvailability = "/".join([Config['Home Assistant']['Discovery_Prefix'], 
        'switch', Config['Home Assistant']['Node_ID'], 'status'])
    PayloadAvailable = 'online'     # Home Assistant default
    PayloadNotAvailable = 'offline' # Home Assistant default
        
    # create Alarm Disable Switch Home Assistant Discovery Config
    TopicAlarmDisable = "/".join([Config['Home Assistant']['Discovery_Prefix'], 
        'switch', Config['Home Assistant']['Node_ID'], 'alarm_disable'])
    ConfigAlarmDisable = {
        'name': Config['Home Assistant']['Node_Name'] + " Alarm Disable",
        'stat_t': "/".join([TopicAlarmDisable, 'state']),
        'cmd_t': "/".join([TopicAlarmDisable, 'set']),
        'qos': QOS,
    }
    # add availability topic if configured
    if ENABLE_AVAILABILITY_TOPIC == True:
        ConfigAlarmDisable['avty_t'] = TopicAvailability
        # ConfigAlarmDisable['pl_avail'] = PayloadAvailable
        # ConfigAlarmDisable['pl_not_avail'] = PayloadNotAvailable

    # create Alarm binary_sensor Home Assistant Discovery Config
    TopicAlarm = "/".join([Config['Home Assistant']['Discovery_Prefix'], 
        'binary_sensor', Config['Home Assistant']['Node_ID'], 'alarm'])
    ConfigAlarm = {
        'name': Config['Home Assistant']['Node_Name'] + " Alarm",
        'stat_t': "/".join([TopicAlarm, 'state']),
        'dev_cla': "heat",
    }
    # add availability topic if configured
    if ENABLE_AVAILABILITY_TOPIC == True:
        ConfigAlarm['avty_t'] = TopicAvailability
        # ConfigAlarm['pl_avail'] = PayloadAvailable
        # ConfigAlarm['pl_not_avail'] = PayloadNotAvailable

    # create RSSI Sensor Home Assistant Discovery Config
    TopicRSSI = "/".join([Config['Home Assistant']['Discovery_Prefix'], 
        'sensor', Config['Home Assistant']['Node_ID'], 'rssi'])
    ConfigRSSI = {
        'name': Config['Home Assistant']['Node_Name'] + " RSSI",
        'stat_t': "/".join([TopicRSSI, 'state']),
        'unit_of_meas': 'dBm',
    }
    # add availability topic if configured
    if ENABLE_AVAILABILITY_TOPIC == True:
        ConfigRSSI['avty_t'] = TopicAvailability
        # ConfigRSSI['pl_avail'] = PayloadAvailable
        # ConfigRSSI['pl_not_avail'] = PayloadNotAvailable

    TopicTemp = [None] * 4
    ConfigTemp = [None] * 4
    # create Temperature Sensor Home Assistant Discovery Config
    TopicTemp[0] = "/".join([Config['Home Assistant']['Discovery_Prefix'], 
        'sensor', Config['Home Assistant']['Node_ID'], 'temperature'])
    ConfigTemp[0] = {
        'name': Config['Home Assistant']['Node_Name'] + " Temperature",
        'stat_t': "/".join([TopicTemp[0], 'state']),
        'unit_of_meas': '°C',
    }
    # add availability topic if configured
    if ENABLE_AVAILABILITY_TOPIC == True:
        ConfigTemp[0].update({'avty_t': TopicAvailability})
        # ConfigTemp[0]['pl_avail'] = PayloadAvailable
        # ConfigTemp[0]['pl_not_avail'] = PayloadNotAvailable

    # create TC1 Temperature Sensor Home Assistant Discovery Config
    TopicTemp[1] = "/".join([Config['Home Assistant']['Discovery_Prefix'], 
        'sensor', Config['Home Assistant']['Node_ID'], 'TC1_temperature'])
    ConfigTemp[1] = {
        'name': Config['Temperature Sensors']['TC1_Name'],
        'stat_t': "/".join([TopicTemp[1], 'state']),
        'unit_of_meas': '°C',
    }
    # add availability topic if configured
    if ENABLE_AVAILABILITY_TOPIC == True:
        
        ConfigTemp[1].update({'avty_t': TopicAvailability})
        # ConfigTemp[1]['pl_avail'] = PayloadAvailable
        # ConfigTemp[1]['pl_not_avail'] = PayloadNotAvailable

    # create TC2 Temperature Sensor Home Assistant Discovery Config
    TopicTemp[2] = "/".join([Config['Home Assistant']['Discovery_Prefix'], 
        'sensor', Config['Home Assistant']['Node_ID'], 'TC2_temperature'])
    ConfigTemp[2] = {
        'name': Config['Temperature Sensors']['TC2_Name'],
        'stat_t': "/".join([TopicTemp[2], 'state']),
        'unit_of_meas': '°C',
    }
    # add availability topic if configured
    if ENABLE_AVAILABILITY_TOPIC == True:
        ConfigTemp[2].update({'avty_t': TopicAvailability})
        # TopicTemp[2]['pl_avail'] = PayloadAvailable
        # TopicTemp[2]['pl_not_avail'] = PayloadNotAvailable

    # create TC3 Temperature Sensor Home Assistant Discovery Config
    TopicTemp[3] = "/".join([Config['Home Assistant']['Discovery_Prefix'], 
        'sensor', Config['Home Assistant']['Node_ID'], 'TC3_temperature'])
    ConfigTemp[3] = {
        'name': Config['Temperature Sensors']['TC3_Name'],
        'stat_t': "/".join([TopicTemp[3], 'state']),
        'unit_of_meas': '°C',
    }
    # add availability topic if configured
    if ENABLE_AVAILABILITY_TOPIC == True:
        ConfigTemp[3].update({'avty_t': TopicAvailability})
        # TopicTemp[3]['pl_avail'] = PayloadAvailable
        # TopicTemp[3]['pl_not_avail'] = PayloadNotAvailable
    
    # setup MQTT
    Mqttc = mqtt.Client()
    # add username and password if defined
    if Config['MQTT']['UserName'] != "":
        logging.info("MQTT authentication will be used.")
        Mqttc.username_pw_set(username=Config['MQTT']['UserName'],
            password=Config['MQTT']['Password'])
    Mqttc.on_message = mqtt_on_message
    Mqttc.on_connect = mqtt_on_connect
    Mqttc.on_disconnect = mqtt_on_disconnect
    Mqttc.will_set(TopicAvailability, payload=PayloadNotAvailable, retain=True)

    # connect to broker
    status = False
    while (status == False):
        try:
            if (Mqttc.connect(Config['MQTT']['Broker'],
                    port=Config['MQTT'].getint('Port'),
                    keepalive=Config['MQTT'].getint('KeepAlive')) == 0):
                status = True       # indicate success
        except:
            # connection failed due to a socket error or other bad thing
            status = False          # indicate failure
        # wait a bit before retrying
        if (status == False):
            logging.error("Failed to connect to broker: mqtt://%s:%s.",
                Config['MQTT']['Broker'],  Config['MQTT'].getint('Port'))
            time.sleep(10)          # sleep for 10 seconds before retrying
    
    # grab SIGTERM to shutdown gracefully
    killer = GracefulKiller()

    # start processing MQTT events
    Mqttc.loop_start()

    # start the background measure temperature timer
    sensorTimer = InfiniteTimer(5, measureSensors, name="SensorTimer")
    sensorTimer.start()

    # loop forever looking for state changes
    while True:
        # needs to be thread safe
        with Lock:
            # handle re-enabling alarms based on current time
            now = datetime.datetime.now()
            if ResetAlarmDisable:
                if now.hour == 18:
                    if NextState['alarm_disable']:
                        NextState['alarm_disable'] = False
                        ResetAlarmDisable = False
                        Changed = True
            else:
                if now.hour != 18:
                    ResetAlarmDisable = True
            # look for Changed but only if connected
            if Changed and Mqttconnected:
                try:
                    # # update availability
                    # Mqttc.publish(TopicAvailability, payload=PayloadAvailable, 
                    #     qos=QOS, retain=True)
                    # create payload for alarm disabled state
                    if NextState['alarm_disable']:
                        payload = 'ON'
                    else:
                        payload = 'OFF'
                    # publish the state
                    Mqttc.publish(ConfigAlarmDisable['stat_t'], 
                        payload=payload, qos=QOS, retain=True)
                    # create payload for alarm state, and update alert buzzer
                    if NextState['alarm'] and not NextState['alarm_disable']:
    # temporarily disable buzzer
                        # buzzer_on()
                        payload = 'ON'
                    else:
                        buzzer_off()
                        payload = 'OFF'
                    # publish the alarm state
                    Mqttc.publish(ConfigAlarm['stat_t'], payload=payload,
                        qos=QOS, retain=True)
                    # check for alarm disable just turning ON
                    if (not CurState['alarm_disable'] and 
                        NextState['alarm_disable']):
                        if CurState['alarm'] or NextState['alarm']:
                            # alarm was silenced
                            logging.info("Alarm Disable turned ON. " +
                                "Active alarm was silenced.")
                        else:
                            # no alarm was silenced
                            logging.info("Alarm Disable turned ON.")
                    # check for alarm disable just turning OFF
                    elif (CurState['alarm_disable'] and 
                        not NextState['alarm_disable']):
                        if CurState['alarm'] or NextState['alarm']:
                            # alarm was in progress
                            logging.info("Alarm Disable turned OFF. " +
                                "Active alarm was resumed.")
                        else:
                            # no alarm was in progress
                            logging.info("Alarm Disable turned OFF. " +
                                "No active alarms.")
                    # check for alarm going active while alarm disable is ON
                    elif (not CurState['alarm'] and NextState['alarm'] and 
                        NextState['alarm_disable']):
                        logging.info("Alarm active but silenced by " +
                            "Alarm Disable.")
                    # check for alarm going active while alarm disable is OFF
                    elif (not CurState['alarm'] and NextState['alarm'] and 
                        not NextState['alarm_disable']):
                        logging.info("Alarm active.")
                    # check for alarm going inactive
                    elif CurState['alarm'] and not NextState['alarm']:
                        logging.info("Alarm discontinued.")
                    # queue up save state to file
                    queueSaveStateFile(CurState)
                except:
                    # log the exception
                    logging.exception("Failed to update state.")
                finally:
                    # always the state is no longer changed
                    #   even if there was an exception
                    Changed = False
                    # next state is now current state (copy dictionary)
                    CurState = NextState.copy()

        # did we receive a signal to exit?
        if killer.kill_now:
            break

        # sleep for a bit
        time.sleep(0.1)

finally:
    # shutdown MQTT gracefully
    if Mqttc is not None:
        # set availability to offline
        if ENABLE_AVAILABILITY_TOPIC == True:
            Mqttc.publish(TopicAvailability, payload=PayloadNotAvailable,
                qos=QOS, retain=True)
            logging.info("Setting Availability to '%s'.", PayloadNotAvailable)
        Mqttc.disconnect()  # disconnect from MQTT broker
        Mqttc.loop_stop()   # will wait until disconnected
        buzzer_off()
        logging.info("Disconnecting from broker: mqtt://%s:%s.",
            Mqttc._host, Mqttc._port)
    
    # try to cancel existing save state file timer
    try:
        SaveStateTimer.cancel()
    except:
        pass
