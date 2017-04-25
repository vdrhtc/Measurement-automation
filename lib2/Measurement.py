'''
Base interface for all measurements.

Should define the raw_data type (???)
я бы сказал что он обязательно должен требовать (как-то) чтобы наследники задавали вид сырых данных, я подумаю как это сделать пока на ум не приходит

Should perform following actions:

 --  automatically call all nesessary devices for a certain measurement. (with the names of devices passed through the constructor)
    of course
 --  implementation of parallel plotting (the part with Treads, the actual plot is adjusted in class with actual measurement)
    yes, but not with threads yet with Processes, still thinking about how exactly it should be hidden from the end-user
 --  some universal data operations on-fly (background substraction, normalization, dispersion calculation, etc.)
    the implementation of these operations should go into each MeasurementResult class, so only the calls of the
    corresponding methods should be left here (may be to allow user to choose exact operation to perform during the dynamic plotting)
 --  universal functions for presetting devices in a number of frequently used regimes (creating windows/channels/sweeps/markers)
    я думаю это лучше поместить в драверы
 --  frequently used functions of standart plotting like single trace (but made fancy, like final figures for presentation/)
    это тоже в классы данных по идее лучше пойдет
 --  a logging of launched measurements from ALL certain classes (chronologically, in a single file, like laboratory notebook, with comments)
    может быть, может быть полезно, если 100500 человек чето мерют одними и теми же приборами и что-то сломалось/нагнулось
some other bullshit?
does this class necessary at all?

some other bullshit:
 -- должен нести описания методов, которые должны быть обязательено реализованы в дочерних классах:
        set_devices (устанавливает, какие приборы используются, получает на вход обекты)
        set_control_parameters (установить неизменные параметры приборов)
        set_varied_parameters (установить изменяемые параметры и их значения; надо написать для STS)
        launch (возможно, целиком должен быть реализован здесь, так как он универсальный)
        _record_data (будет содержать логику измерения, пользуясь приборами и параметрами, определенными выше)\
'''
from numpy import *
import copy
import pyvisa
#import sys.stdout.flush
#from sys.stdout import flush
import os, fnmatch
import pickle
from drivers import *
# from drivers.Agilent_PNA_L import *
# from drivers.Agilent_PNA_L import *
# from drivers.Yokogawa_GS200 import *
# from drivers.KeysightAWG import *
# from drivers.E8257D import MXG,EXG
# from drivers.Agilent_DSO import *
from matplotlib import pyplot as plt

def format_time_delta(delta):
    hours, remainder = divmod(delta, 3600)
    minutes, seconds = divmod(remainder, 60)
    return '%s h %s m %s s' % (int(hours), int(minutes), round(seconds, 2))

class Measurement():

    '''
    Any inheritance?
    The class contains methods to help with the implementation of measurement classes.

    '''
    _vna1 = None
    _vna2 = None
    _exa = None
    _exg = None
    _mxg = None
    _awg1 = None
    _awg2 = None
    _awg3 = None
    _dso = None
    _yok1 = None
    _yok2 = None
    _yok3 = None
    _logs = []

    def __init__(self, name, sample_name, devs_names=None):
        '''
        Parameters:
        --------------------
        name: string
            name of the measurement
        sample_name: string
            the name of the sample that is measured
        devs_names: array-like
            with devices' standard names.
        --------------------

        Constructor creates variables for devices passed to it and initialises all devices.

        Standard names of devices within this driver are:

            'vna1',vna2','exa','exg','mxg','awg1','awg2','awg3','dso','yok1','yok2','yok3'

        with _ added in front for a variable of a class

        if key is not recognised returns a mistake

        '''

        self._interrupted = False
        self._name = name
        self._sample_name = sample_name

        self._devs_dict = \
                {'vna1' : [ ["PNA-L","PNA-L1"], [Agilent_PNA_L,"Agilent_PNA_L"] ],\
                 'vna2': [ ["PNA-L-2","PNA-L2"], [Agilent_PNA_L,"Agilent_PNA_L"] ],\
                 'exa' : [ ["EXA"], [Agilent_EXA,"Agilent_EXA_N9010A"] ],\
                 'exg' : [ ["EXG"], [E8257D,"EXG"] ],\
                 'mxg' : [ ["MXG"], [E8257D,"MXG"] ],\
                 'awg1': [ ["AWG","AWG1"], [KeysightAWG,"KeysightAWG"] ],\
                 'awg2': [ ["AWG_Vadik","AWG2"], [KeysightAWG,"KeysightAWG"] ],\
                 'awg3': [ ["AWG3"], [KeysightAWG,"KeysightAWG"] ],\
                 'dso' : [ ["DSO"], [Keysight_DSOX2014,"Keysight_DSOX2014"] ],\
                 'yok1': [ ["GS210_1"], [Yokogawa_GS200,"Yokogawa_GS210"] ], \
                 'yok2': [ ["GS210_2"], [Yokogawa_GS200,"Yokogawa_GS210"] ], \
                 'yok3': [ ["GS210_3"], [Yokogawa_GS200,"Yokogawa_GS210"] ]     }

        self._devs_names = devs_names
        self._actual_devices = {}
        self._list = ""
        rm = pyvisa.ResourceManager()
        temp_list = list(rm.list_resources_info().values())

        self._devs_info = [item[4] for item in list(temp_list)]
                # returns list of tuples: (IP Address string, alias) for all
                # devices present in VISA

        for name in self._devs_names:
            for device_alias in self._devs_info:
                if (name in self._devs_dict.keys()) \
                        and (device_alias in self._devs_dict[name][0]):
                    device_object = getattr(*self._devs_dict[name][1])(device_alias)
                    self._actual_devices[name]=device_object
                    print("The device %s is detected as %s"%(name, device_alias))
                    #getattr(self,"_"+name)._visainstrument.query("*IDN?")
                    break

    def launch(self):
        plt.ion()

        start_datetime = self._measurement_result.get_start_datetime()
        print("Started at: ", start_datetime.ctime())

        t = Thread(target=self._record_data)
        t.start()
        try:
            while not self._measurement_result.is_finished():
                self._measurement_result._visualize_dynamic()
                plt.pause(5)
        except KeyboardInterrupt:
            self._interrupted = True

        self._measurement_result.finalize()
        return self._measurement_result

    def _record_data(self):
        '''
        This method must be overridden for each new measurement type. Now
        it contains only setting of the start time.

        Should contain all of the recording logic and set the data of the
        corresponding MeasurementResult object.
        See lib2.SingleToneSpectroscopy.py as an example implementation
        '''
        self._start_datetime = dt.now()

    def _detect_resonator(self, vna):
        """
        Finds frequency of the resonator visible on the VNA screen
        """
        vna.avg_clear(); vna.prepare_for_stb(); vna.sweep_single(); vna.wait_for_stb()
        port = circuit.notch_port(vna.get_frequencies(), vna.get_sdata())
        port.autofit()
        port.plotall()
        min_idx = argmin(abs(port.z_data_sim))
        return (vna.get_frequencies()[min_idx],
                    min(abs(port.z_data_sim)), angle(port.z_data_sim)[min_idx])
