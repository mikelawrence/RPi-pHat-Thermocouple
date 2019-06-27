# Fridge Monitor Application

The Fridge Monitor application is written in Python 3 and supports the hardware elements of the Raspberry Pi Zero Thermocouple pHat which can measure up to 3 thermocouple temperatures. The application is discoverable by [Home Assistant](https://home-assistant.io/), an open-source home automation platform also running Python 3. [MQTT](http://mqtt.org/), a machine-to-machine (M2M)/"Internet of Things" connectivity protocol, is the basis of communication with Home Assistant.

## Configuration Notes
All settings for this application are in the '[fridgemonitor.conf](fridgemonitor.conf)'. This is where you point to the correct MQTT broker and configure how the temperature sensors work. There are a few settings of note here. Discovery_Enabled = false will prevent Home Assistant from automatically discovering the Fridge Monitor.

## Home Assistant Notes

If you don't want to use discovery here is the configuration of the Fridge Monitor in Home Assistant. Note the 'studio_fridge_monitor' you see in the example yaml is the Node_ID which is specified in the 'fridgemonitor.conf' file.

```yaml
switch:
  - platform: mqtt
    sensors:
      # This switch provides a means to turn off both the audible alert and alarm boolean_sensor
      #   This switch will automatically turn back OFF at 6PM every day.
      name: "Studio Fridge Monitor Alert Disable"
        state_topic: "hass/switch/studio_fridge_monitor/alert_disable/state"
        command_topic: "hass/switch/studio_fridge_monitor/alert_disable/set"
        availability_topic: "hass/switch/studio_fridge_monitor/status"
        qos: 1
# The following sensors are updated at Sensor_Publish_Rate specified in the frigdemonitor.conf file
sensor:
  - platform: mqtt
    sensors:
        # WiFi Received Signal Strength Indicator (RSSI)
        name: "Studio Fridge Monitor RSSI"
            state_topic: "hass/sensor/studio_fridge_monitor/rssi/state"
            availability_topic: "hass/switch/studio_fridge_monitor/status"
            unit_of_measurement: 'dBm'
        # Temperature measured by the on-board DS18S20 Sensor
        name: "Studio Fridge Monitor Temperature"
            state_topic: "hass/sensor/studio_fridge_monitor/temperature/state"
            availability_topic: "hass/switch/studio_fridge_monitor/status"
            unit_of_measurement: '°C'
        # Temperature measured by the on-board MAX31850K Thermocouple Sensor (TC1)
        name: "Studio Fridge Temperature"
            state_topic: "hass/sensor/studio_fridge_monitor/TC1_temperature/state"
            availability_topic: "hass/switch/studio_fridge_monitor/status"
            unit_of_measurement: '°C'
        # Temperature measured by the on-board MAX31850K Thermocouple Sensor (TC2)
        name: "Studio Freezer Temperature"
            state_topic: "hass/sensor/studio_fridge_monitor/TC2_temperature/state"
            availability_topic: "hass/switch/studio_fridge_monitor/status"
            unit_of_measurement: '°C'
        # Temperature measured by the on-board MAX31850K Thermocouple Sensor (TC3)
        name: "Studio Freezer Temperature 2"
            state_topic: "hass/sensor/studio_fridge_monitor/TC3_temperature/state"
            availability_topic: "hass/switch/studio_fridge_monitor/status"
            unit_of_measurement: '°C'
binary_sensor:
  - platform: mqtt
    sensors:
      # When active the Fridge Monitor has detected an over temperature condition
      name: "Studio Fridge Monitor Alarm"
        state_topic: "hass/binary_sensor/studio_fridge_monitor/alarm/state"
        availability_topic: "hass/switch/studio_fridge_monitor/status"
        device_class: "heat"
```

## Raspberry Pi Setup

It is assumed that you already followed the instructions in the Raspberry Pi Setup section of the main project [README file](../README.md). If you have not, please do so now before continuing. Be sure to edit the 'fridgemonitor.conf' file to support your configuration. Test the software by executing the following commands. Note if you are not using the standard `pi` user you will have to edit the commands by replacing `/home/pi` with your user's home directory.

```text
cd /home/pi/RPi-pHat-Thermocouple/code/
chmod 755 rgbfloodlight.py
./rgbfloodlight.py
```

If you see no errors you should be able to see your light in Home Assistant. Configuring Home Assistant is a bit of a stretch for this guide but here are a couple of hints.

* "Fridge Monitor: Failed to load state file 'fridgemonitorstate.json'." means there is no previous state for the light. This is perfectly normal when the code is run for the first time.
* Make sure you have MQTT installed. If you use HASS.IO goto the HASS.IO configuration and install the Mosquitto Broker.
* Make sure you have MQTT discovery enabled. See [MQTT Discovery](https://home-assistant.io/docs/mqtt/discovery/).
* Make sure your MQTT discovery prefix matches the Discovery_Prefix in your Fridge Monitor configuration file.

I use HASS.IO with the Mosquitto Broker add-on installed and my configuration for MQTT is as follows...

```yaml
mqtt:
  broker: core-mosquitto
  discovery: true
  discovery_prefix: hass
```

## Systemd run at boot

If you are not using the standard `pi` user you will also have to edit the `fridgemonitor.service` file so that links point to the correct directory.

Execute the following commands to install Fridge Monitor as a systemd service to run on startup.

```text
cd /home/pi/RPi-pHat-Thermocouple/code
sudo cp fridgemonitor.service /lib/systemd/system
sudo chmod 644 /lib/systemd/system/fridgemonitor.service
sudo systemctl enable fridgemonitor.service
sudo systemctl start fridgemonitor.service
```

## Acknowledgments

The following python libraries are required.

* [Eclipse Paho™ MQTT Python Client](https://github.com/eclipse/paho.mqtt.python)
* [Python3 w1thermsensor](https://github.com/timofurrer/w1thermsensor)
* [Adafruit Python GPIO](https://github.com/adafruit/Adafruit_Python_GPIO)

The following code was pulled from the Internet

* [timer.py](https://github.com/jalmeroth/homie-python/blob/master/homie/timer.py)
