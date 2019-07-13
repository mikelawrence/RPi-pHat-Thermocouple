# -*- coding: utf-8 -*-
#
# The MIT License (MIT)
#
# Copyright (c) 2015 Timo Furrer - originally from 
# https://github.com/timofurrer/w1thermsensor/blob/master/w1thermsensor/core.py
#
# Mike Lawrence (2019) 
# added SensorFaultError, raw_max31850k_value() and get_max31850k_address()
# to better support MAX31850K devices.
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
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# SOFTWARE.
# 
"""
    This module provides a temperature sensor of type w1 therm.
"""
import logging
import math
from os import path, listdir, system, environ
from time import sleep


class W1ThermSensorError(Exception):
    """Exception base-class for W1ThermSensor errors"""
    pass


class KernelModuleLoadError(W1ThermSensorError):
    """Exception when the w1 therm kernel modules could not be loaded"""
    def __init__(self):
        super(KernelModuleLoadError, self).__init__(
            "Cannot load w1 therm kernel modules")


class NoSensorFoundError(W1ThermSensorError):
    """Exception when no sensor is found"""
    def __init__(self, sensor_type, sensor_id):
        super(NoSensorFoundError, self).__init__("No {0} temperature sensor with id '{1}' found".format(
            W1ThermSensor.TYPE_NAMES.get(sensor_type, "Unknown"), sensor_id))


class SensorNotReadyError(W1ThermSensorError):
    """Exception when the sensor is not ready yet"""
    def __init__(self):
        super(SensorNotReadyError, self).__init__("Sensor is not yet ready to read temperature")


class UnsupportedUnitError(W1ThermSensorError):
    """Exception when unsupported unit is given"""
    def __init__(self):
        super(UnsupportedUnitError, self).__init__("Only Degrees C, F and Kelvin are currently supported")


class SensorFaultError(W1ThermSensorError):
    """Exception when sensor read resulted in a fault condition"""
    def __init__(self):
        super(SensorFaultError, self).__init__("Sensor presented a fault condition when read")


def load_kernel_modules():
    """
    Load kernel modules needed by the temperature sensor
    if they are not already loaded.
    If the base directory then does not exist an exception is raised an the kernel module loading
    should be treated as failed.

    :raises KernelModuleLoadError: if the kernel module could not be loaded properly
    """
    if not path.isdir(W1ThermSensor.BASE_DIRECTORY):
        system("modprobe w1-gpio >/dev/null 2>&1")
        system("modprobe w1-therm >/dev/null 2>&1")

    for _ in range(W1ThermSensor.RETRY_ATTEMPTS):
        if path.isdir(W1ThermSensor.BASE_DIRECTORY):  # w1 therm modules loaded correctly
            break
        sleep(W1ThermSensor.RETRY_DELAY_SECONDS)
    else:
        raise KernelModuleLoadError()


class W1ThermSensor(object):
    """This class represents a temperature sensor of type w1-therm"""
    THERM_SENSOR_DS18S20 = 0x10
    THERM_SENSOR_DS1822 = 0x22
    THERM_SENSOR_DS18B20 = 0x28
    THERM_SENSOR_DS1825 = 0x3B
    THERM_SENSOR_DS28EA00 = 0x42
    THERM_SENSOR_MAX31850K = 0x3B
    ALL_TYPES = [
        THERM_SENSOR_DS18S20, THERM_SENSOR_DS1822, THERM_SENSOR_DS18B20,
        THERM_SENSOR_DS1825, THERM_SENSOR_DS28EA00, THERM_SENSOR_MAX31850K
    ]
    DEGREES_C = 0x01
    DEGREES_F = 0x02
    KELVIN = 0x03
    BASE_DIRECTORY = "/sys/bus/w1/devices"
    SLAVE_FILE = "w1_slave"
    UNIT_FACTORS = {
        DEGREES_C: lambda x: x * 0.001,
        DEGREES_F: lambda x: x * 0.001 * 1.8 + 32.0,
        KELVIN: lambda x: x * 0.001 + 273.15
    }
    UNIT_FACTOR_NAMES = {
        "celsius": DEGREES_C,
        "fahrenheit": DEGREES_F,
        "kelvin": KELVIN
    }
    TYPE_NAMES = {
        THERM_SENSOR_DS18S20: "DS18S20", THERM_SENSOR_DS1822: "DS1822", THERM_SENSOR_DS18B20: "DS18B20",
        THERM_SENSOR_DS1825: "DS1825", THERM_SENSOR_DS28EA00: "DS28EA00", THERM_SENSOR_MAX31850K: "MAX31850K"
    }
    RESOLVE_TYPE_STR = {
        "10": THERM_SENSOR_DS18S20, "22": THERM_SENSOR_DS1822, "28": THERM_SENSOR_DS18B20,
        "42": THERM_SENSOR_DS28EA00, "3b": THERM_SENSOR_MAX31850K
    }
    RETRY_ATTEMPTS = 10
    RETRY_DELAY_SECONDS = 1.0 / float(RETRY_ATTEMPTS)

    @classmethod
    def get_available_sensors(cls, types=None):
        """
            Return all available sensors.

            :param list types: the of the sensor to look for. If types is None it will search for all available types.

            :returns: a list of sensor instances.
            :rtype: list

        """
        if not types:
            types = cls.ALL_TYPES
        is_sensor = lambda s: any(s.startswith(hex(x)[2:]) for x in types)
        return [cls(cls.RESOLVE_TYPE_STR[s[:2]], s[3:]) for s in listdir(cls.BASE_DIRECTORY) if is_sensor(s)]

    def __init__(self, sensor_type=None, sensor_id=None):
        """
            Initializes a W1ThermSensor.
            If the W1ThermSensor base directory is not found it will automatically load
            the needed kernel modules to make this directory available.
            If the expected directory will not be created after some time an exception is raised.

            If no type and no id are given the first found sensor will be taken for this instance.

            :param int sensor_type: the type of the sensor.
            :param string id: the id of the sensor.

            :raises KernelModuleLoadError: if the w1 therm kernel modules could not be loaded correctly
            :raises NoSensorFoundError: if the sensor with the given type and/or id does not exist or is not connected
        """
        self.type = sensor_type
        self.id = sensor_id
        if not sensor_type and not sensor_id:  # take first found sensor
            for _ in range(self.RETRY_ATTEMPTS):
                s = self.get_available_sensors()
                if s:
                    self.type, self.id = s[0].type, s[0].id
                    break
                sleep(self.RETRY_DELAY_SECONDS)
            else:
                raise NoSensorFoundError(None, "")
        elif not sensor_id:
            s = self.get_available_sensors([sensor_type])
            if not s:
                raise NoSensorFoundError(sensor_type, "")
            self.id = s[0].id

        # store path to sensor
        self.sensorpath = path.join(self.BASE_DIRECTORY, self.slave_prefix + self.id, self.SLAVE_FILE)

        if not self.exists():
            raise NoSensorFoundError(self.type, self.id)

    def __repr__(self):
        """
            Returns a string that eval can turn back into this object

            :returns: representation of this instance
            :rtype: string
        """
        return "{}(sensor_type={}, sensor_id='{}')".format(
            self.__class__.__name__, self.type, self.id)

    def __str__(self):
        """
            Returns a pretty string respresentation

            :returns: representation of this instance
            :rtype: string
        """
        return "{0}(name='{1}', type={2}(0x{2:x}), id='{3}')".format(
            self.__class__.__name__, self.type_name, self.type, self.id)

    @property
    def type_name(self):
        """Returns the type name of this temperature sensor"""
        return self.TYPE_NAMES.get(self.type, "Unknown")

    @property
    def slave_prefix(self):
        """Returns the slave prefix for this temperature sensor"""
        return "%s-" % hex(self.type)[2:]

    def exists(self):
        """Returns the sensors slave path"""
        return path.exists(self.sensorpath)

    @property
    def raw_sensor_value(self):
        """
            Returns the raw sensor value

            :returns: the raw value read from the sensor
            :rtype: float

            :raises NoSensorFoundError: if the sensor could not be found
            :raises SensorNotReadyError: if the sensor is not ready yet
        """
        if self.type == 0x3b:
            # MAX31850k is treated differently
            return self.raw_max31850k_value
        # all other sensors are treated the same way
        try:
            with open(self.sensorpath, "r") as f:
                data = f.readlines()
        except IOError:
            raise NoSensorFoundError(self.type, self.id)

        if data[0].strip()[-3:] != "YES":
            raise SensorNotReadyError()
        return float(data[1].split("=")[1])

    @property
    def raw_max31850k_value(self):
        """
            Returns the raw sensor value for a MAX31850K thermocouple
            converter.
            Performs NIST linearization for Type K thermocouple.
            See https://learn.adafruit.com/
                calibrating-sensors/maxim-31855-linearization for more info.

            :returns: the linearized value read from the sensor
            :rtype: float

            :raises NoSensorFoundError: if the sensor could not be found
            :raises SensorNotReadyError: if the sensor is not ready yet
            :raises SensorFaultError : if the sensor read reported a fault
        """
        try:
            with open(self.sensorpath, "r") as f:
                data = f.readlines()
        except IOError:
            raise NoSensorFoundError(self.type, self.id)

        if data[0].strip()[-3:] != "YES":
            raise SensorNotReadyError()
        
        dataASCIIBytes = data[0].split(" ")

        # check for faults
        if int(dataASCIIBytes[0], 16) & 0x1 == 0x01:
            raise SensorFaultError()

        # get MAX31850 the raw thermocouple temperature in Celsius
        Traw = int(dataASCIIBytes[1], 16) << 6
        Traw += int(dataASCIIBytes[0], 16) >> 2
        # convert sign/magnitude to 2's complement with sign at bit 14
        if (Traw & (1 << (14 - 1))) != 0:
            Traw = Traw - (1 << 14)
        # convert fixed point to float
        Traw /= 4.0

        # get MAX31850 cold junction temperature in Celsius
        Tcj = int(dataASCIIBytes[3], 16) << 4
        Tcj += int(dataASCIIBytes[2], 16) >> 4
        # convert sign/magnitude to 2's complement with sign at bit 12
        if (Tcj & (1 << (12 - 1))) != 0:
            Tcj = Tcj - (1 << 12)
        # convert fixed point to float
        Tcj /= 16.0

        # NIST K-Type thermocouple linearization
        # see https://srdata.nist.gov/its90/download/type_k.tab
        # also 
        # first calculate cold junction equivalent thermocouple voltage from
        #   cold junction temperature using NIST temp to voltage coefficients
        if Tcj < 0.0:
            # range -270°C to 0°C
            c = [
                 0.000000000000E+00,
                 0.394501280250E-01,
                 0.236223735980E-04,
                -0.328589067840E-06,
                -0.499048287770E-08,
                -0.675090591730E-10,
                -0.574103274280E-12,
                -0.310888728940E-14,
                -0.104516093650E-16,
                -0.198892668780E-19,
                -0.163226974860E-22,
            ]
            # time to compute cold junction equivalent thermocouple voltage
            Vcj = 0.0
            for i in range(0, len(c)):
                Vcj += c[i] * math.pow(Tcj, i)
        else:
            # range 0°C to 1372°C
            # coefficients
            c = [
                -0.176004136860E-01,
                 0.389212049750E-01,
                 0.185587700320E-04,
                -0.994575928740E-07,
                 0.318409457190E-09,
                -0.560728448890E-12,
                 0.560750590590E-15,
                -0.320207200030E-18,
                 0.971511471520E-22,
                -0.121047212750E-25,
            ]
            # exponential constants
            a = [
                 0.118597600000E+00,
                -0.118343200000E-03,
                 0.126968600000E+03,
            ]
            # time to compute linearized cold junction equivalent 
            #   thermocouple voltage
            Vcj = 0.0
            for i in range(0, len(c)):
                Vcj += c[i] * math.pow(Tcj, i)
            Vcj += a[0] * math.exp(a[1] * math.pow((Tcj-a[2]), 2.0))
        # calculate thermocouple voltage using MAX31855's 𝝻V/°C for K-Type 
        #   thermocouple (see Table 1 of MAX31855 datasheet)
        Vt = 0.041276 * (Traw - Tcj)
        # add the linearized cold junction equivalent thermocouple voltage 
        #   to thermocouple voltage (mV)
        Vtotal = Vt + Vcj
        # calculate linearized thermocouple temperature from Vtotal using
        #   NIST voltage-to-temperature (inverse) coefficients
        #   coefficent set to use (out of three) is determined by Vtotal
        #   which is effectively linearized temperature
        if Vtotal < 0.0:
            # range -270°C to 0°C
            d = [
                 0.0000000E+00,
                 2.5173462E+01,
                -1.1662878E+00,
                -1.0833638E+00,
                -8.9773540E-01,
                -3.7342377E-01,
                -8.6632643E-02,
                -1.0450598E-02,
                -5.1920577E-04,
            ]
        elif Vtotal < 20.644: 
            # range 0°C to 500°C
            d = [
                 0.000000E+00,
                 2.508355E+01,
                 7.860106E-02,
                -2.503131E-01,
                 8.315270E-02,
                -1.228034E-02,
                 9.804036E-04,
                -4.413030E-05,
                 1.057734E-06,
                -1.052755E-08,
            ]
        else:
            # range 500°C to 1372°C
            d = [
                -1.318058E+02,
                 4.830222E+01,
                -1.646031E+00,
                 5.464731E-02,
                -9.650715E-04,
                 8.802193E-06,
                -3.110810E-08,
            ]
        # time to compute linearized thermocouple temperature
        Tt = 0.0
        for i in range(0, len(d)):
            Tt += d[i] * math.pow(Vtotal, i)

        # rational polynomial function approximation linearizations for
        #   K-Type  thermocouples with a temperature range of -100°C to 100°C
        #   see http://www.mosaic-industries.com/embedded-systems/
        #       microcontroller-projects/temperature-measurement/
        #       thermocouple/type-k-calibration-table
        # first get cold junction voltage from cold junction temperature
        #   cold temperature junction range of -20°C to 70°C
        T0 =  2.5000000E+01
        V0 =  1.0003453E+00
        p1 =  4.0514854E-02
        p2 = -3.8789638E-05
        p3 = -2.8608478E-06
        p4 = -9.5367041E-10
        q1 = -1.3948675E-03
        q2 = -6.7976627E-05
        a = Tcj - T0
        VcjNew = (V0 + a*(p1 + a*(p2 + a*(p3 + p4*a))) / (1 + a*(q1 + q2*a)))
        # linearize thermocouple temperature
        T0 = -8.7935962E+00
        V0 = -3.4489914E-01
        p1 =  2.5678719E+01
        p2 = -4.9887904E-01
        p3 = -4.4705222E-01
        p4 = -4.4869203E-02
        q1 =  2.3893439E-04
        q2 = -2.0397750E-02
        q3 = -1.8424107E-03
        # cold junction voltage + thermocouple voltage
        totalVoltage = 0.041276 * (Traw - Tcj) + VcjNew
        # intermediate sum for equation below
        a = totalVoltage - V0
        # linearize thermocouple measurement
        TtNew = (T0 + a*(p1 + a*(p2 + a*(p3 + p4*a))) /
                    (1 + a*(q1 + a)*(q2 + q3*a)))
        logging.debug(f"Vcj={Vcj}, VcjNew={VcjNew}")
        logging.debug(f"Tt={Tt:0.6F}, TtNew={TtNew:0.6F}, " +
            f"Traw={Traw:0.2F}, Tcj={Tcj:0.2F}")

        return Tt * 1000.0
        
    def get_max31850k_address(self):
        """
            Gets the address of the MAX31850 determined by the 
              AD0-AD3 inputs on the device.

            :returns: the address with a range of 0-15
            :rtype: int

            :raises NoSensorFoundError: if the sensor is not a MAX31850K
            :raises SensorNotReadyError: if the sensor is not ready yet
        """
        # throw error if not a MAX31850 1-wire
        if self.type != 0x3b:
            raise NoSensorFoundError(self.type, self.id)
        # reading temperature will read the entire scratchpad
        try:
            with open(self.sensorpath, "r") as f:
                data = f.readlines()
        except IOError:
            raise NoSensorFoundError(self.type, self.id)

        # make sure sensor is ready
        if data[0].strip()[-3:] != "YES":
            raise SensorNotReadyError()
        dataASCIIBytes = data[0].split(" ")

        return int(dataASCIIBytes[4], 16) & 0x0F
        
    @classmethod
    def _get_unit_factor(cls, unit):
        """
            Returns the unit factor depending on the unit constant

            :param int unit: the unit of the factor requested

            :returns: a function to convert the raw sensor value to the given unit
            :rtype: lambda function

            :raises UnsupportedUnitError: if the unit is not supported
        """
        try:
            if isinstance(unit, str):
                unit = cls.UNIT_FACTOR_NAMES[unit]
            return cls.UNIT_FACTORS[unit]
        except KeyError:
            raise UnsupportedUnitError()

    def get_temperature(self, unit=DEGREES_C):
        """
            Returns the temperature in the specified unit

            :param int unit: the unit of the temperature requested

            :returns: the temperature in the given unit
            :rtype: float

            :raises UnsupportedUnitError: if the unit is not supported
            :raises NoSensorFoundError: if the sensor could not be found
            :raises SensorNotReadyError: if the sensor is not ready yet
        """
        factor = self._get_unit_factor(unit)
        return factor(self.raw_sensor_value)

    def get_temperatures(self, units):
        """
            Returns the temperatures in the specified units

            :param list units: the units for the sensor temperature

            :returns: the sensor temperature in the given units. The order of
            the temperatures matches the order of the given units.
            :rtype: list

            :raises UnsupportedUnitError: if the unit is not supported
            :raises NoSensorFoundError: if the sensor could not be found
            :raises SensorNotReadyError: if the sensor is not ready yet
        """
        sensor_value = self.raw_sensor_value
        return [self._get_unit_factor(unit)(sensor_value) for unit in units]


# Load kernel modules automatically upon import.
# Set the environment variable W1THERMSENSOR_NO_KERNEL_MODULE=1

if environ.get('W1THERMSENSOR_NO_KERNEL_MODULE', '0') != '1':
    load_kernel_modules()
