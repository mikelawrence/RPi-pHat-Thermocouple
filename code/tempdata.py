# -*- coding: UTF-8 -*-
# Keeps a moving average of Temperature Sensor data
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
#
from   collections import deque
from   datetime import datetime
import itertools
import logging
import math
import pickle

# logging.basicConfig(format='Fridge Monitor: %(message)s', 
#   level=logging.DEBUG)
# logger for this module
#logger = logging.getLogger(__name__)

# Ordinal number replacement 1, 2, 3, 4 -> 1st, 2nd, 3rd, 4th
ordinal = lambda n: "%d%s" % (n, 
    "tsnrhtdd"[(math.floor(n/10)%10!=1)*(n%10<4)*n%10::4])

class TempData:
    """Keep track of temperature sensor data over time."""
    def __init__(self, name = "", file_name = "", delta_rise = 1.5, 
        alarm_max_temp = 21.0, alarm_set_time = 30.0, alarm_set_temp = 15.0, 
        alarm_reset_temp = 10.0):
        # local copy of sensor name
        self._name = name
        # local copy of delta rise to register door open in °C/min
        self._delta_rise = delta_rise
        # local copy of alarm max temperature to instantly set the alarm in °C
        self._alarm_max_temp = alarm_max_temp
        # local copy of alarm set time in minutes
        self._alarm_set_time = alarm_set_time
        # local copy of alarm set temperature in °C
        self._alarm_set_temp = alarm_set_temp
        # local copy of alarm reset temperature in °C
        self._alarm_reset_temp = alarm_reset_temp
        # local copy of file name used to save temperature data to disk
        self._file_name = file_name
        # load the file if specified
        if file_name != "":
            self.load_file()
            return
        # create empty list for frequent samples
        self._samples = deque()
        # create empty list for 1 minute samples
        self._samples_1min = deque()
        # alarm state
        self._alarm = False
        # time temperature went above alarm set temp
        self._alarm_time = 0
        # average of last 24 hours
        self._avg_24hr = float('nan')
        # most recent delta (delta beteen 1 minute samples)
        self._delta = float('nan')
        # number of noisy samples in a row
        self._noisy = 0
        # when True door is open, otherwise closed
        #   updated evertime data_analysis() is called oronce a minute, 
        #   can be active for multiple minutes while waiting for temperature
        #   to settle down
        self._open = False

    def __len__(self):
        """Returns the number of stored 1 minute samples."""
        return len(self._samples_1min)

    def append(self, temp):
        """Add temperature sample to data store."""
        # remove samples that are too old (right side)
        now = datetime.now().timestamp()
        while len(self._samples) > 0 and now - self._samples[-1][0] >= 57:
            self._samples.pop()
        # force temp to float
        temp = float(temp)
        # throw out unknown temps
        if temp == None:
            logging.error(f"Error, {self._name} append None thrown out.")
            return
        if math.isnan(temp):
            logging.error(f"Error, {self._name} append NaN thrown out.")
            return
        # try to eliminate noisy samples 
        if len(self._samples) > 0:
            delta = temp - self._samples[0][1]
            if abs(delta) >= 3.0:
                if self._noisy <= 3:
                    # not too many noisy samples in a row
                    self._noisy += 1
                    logging.warning(f"Warning, {self._name} dropped " + 
                        f"{ordinal(self._noisy)} noisy " +
                        f"sample. Delta = {delta:0.2f}°C.")
                    return
                else:
                    # too many noisy samples in a row
                    logging.warning(f"Warning, {self._name} appended 4th " +
                        f"noisy sample. Delta = {delta:0.2f}°C.")
        # appending a sample means there are no more noisy samples
        self._noisy = 0
        # create the next sample
        sample = [now, temp]
        # add sample to newest (left side)
        self._samples.appendleft(sample)

    def data_analysis(self):
        """Compute current average and other data analysis."""
        # this method should be called every minute
        # do nothing if there are no samples
        if len(self._samples) == 0:
            return
        # get time of newest temperature sample
        last_time = self._samples[0][0]
        # remove samples that are too old (24 hours) from right side of queue
        while (len(self._samples_1min) > 0 and 
            last_time - self._samples_1min[-1][0] >= 24 * 60 * 60 - 30):
            self._samples_1min.pop()
        # compute the 1 minute average
        average = 0.0
        for sample in self._samples:
            average += sample[1] # 1 is temperature
        average /= len(self._samples)
        # create the next sample
        sample = [last_time, average]
        # add sample to newest (left side)
        self._samples_1min.appendleft(sample)
        # compute delta using 1 minute samples
        if (len(self._samples_1min) > 1):
            # get the most recent sample time (in minutes) and temperature
            latest_time = sample[0] / 60.0
            latest_sample = sample[1]
            # get the 2nd most recent sample time (in minutes) and temperature
            prev_time = self._samples_1min[1][0] / 60.0
            prev_sample = self._samples_1min[1][1]
            # compute the most recent delta
            self._delta = ((latest_sample - prev_sample) /
                           (latest_time - prev_time))
        else:
            self._delta = float('nan')
         # compute the 24 hour average
        self._avg_24hr = 0.0
        for sample in self._samples_1min:
            self._avg_24hr += sample[1]
        self._avg_24hr /= len(self._samples_1min)
        # handle door open
        if self._open:
            # door was previously open, 
            if self._delta < self._delta_rise - 0.5:
                # delta is below the closed threshold (delta_rise - 0.5)
                self._open = False
                logging.info("%s door closed.", self._name)
        else:
            # door was previously closed
            if self._delta >= self._delta_rise:
                self._open = True
                logging.info("%s door open.", self._name)
        # handle alarm
        if self.temperature >= self._alarm_max_temp:
            # we have exceeded the maximum temperature, instant alarm
            self._alarm = True
        if not self._alarm:
            # no alarm in progress
            if self.temperature >= self._alarm_set_temp:
                # we are above alarm set temp
                if self._alarm_time == 0:
                    # just went above alarm set temp
                    self._alarm_time = datetime.now().timestamp()
                else:
                    # we are waiting for alarm set time to pass
                    if (datetime.now().timestamp() - self._alarm_time >= 
                    self._alarm_set_time * 60.0):
                        # we have been above alarm set time long enough
                        self._alarm = True
            else:
                # we are not above alarm set temp, always reset alarm time
                self._alarm_time = 0
        else:
            # alarm is in progress
            if self.temperature <= self._alarm_reset_temp:
                # alarm is now inactive
                self._alarm =  False
                # alarm time should be reset when alarm is deactivated
                self._alarm_time = 0

        # if not 'Monitor' in self._name:
        #     logging.info("%s Temp = %0.2f°C, Avg = %0.2f°C, Delta = %0.4f°C/min", 
        #         self._name, self.temperature, self.average, self.delta)
        #     logging.info(f"{self._name}, Sample queue length = {len(self._samples)}, "
        #         f"1 Minute Sample queue length = {len(self._samples_1min)}.")

    def save_file(self, filename=None):
        """Save current state to file."""
        if self._file_name != "":
            filename = self._file_name
        with open(filename, 'wb') as outFile:
            pickle.dump(self._avg_24hr, outFile)
            pickle.dump(self._delta, outFile)
            pickle.dump(self._samples_1min, outFile)
        
    def load_file(self, filename=None):
        """Load current state from file."""
        try:
            if self._file_name != "":
                filename = self._file_name
            with open(filename, 'rb') as inFile:
                self._avg_24hr = pickle.load(inFile)
                self._delta = pickle.load(inFile)
                self._samples_1min = pickle.load(inFile)
            self._samples = deque()
            self._noisy = 0
            self._open = False
            self._alarm = False
            logging.info("Loaded %s data file.", filename)
            return
        except FileNotFoundError:
            # file not found is not an error
            pass
        except:
            # log all other errors
            logging.exception("Error, unable to load file %s.", filename)
        # initialize the object 
        self._samples = deque()
        self._samples_1min = deque()
        self._avg_24hr = float('nan')
        self._delta = float('nan')
        self._noisy = 0
        self._open = False
        self._alarm = False

    @property
    def name(self):
        """Get the name of this temperature sensor."""
        return self._name

    @property
    def temperature(self):
        """Get the last 1 minute temperature."""
        if len(self._samples_1min) > 0:
            return self._samples_1min[0][1]
        else:
            return float('nan')

    @property
    def average(self):
        """Get the last average temperature."""
        return self._avg_24hr

    @property
    def delta(self):
        """Get the gradient of the last 1 minute of stored samples."""
        return self._delta

    @property
    def alarm(self):
        """Get the current alarm state."""
        return self._alarm

    @property
    def door_open(self):
        """Get the current door open/closed status."""
        return self._open
