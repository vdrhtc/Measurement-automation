from drivers.instrument import Instrument

import sys
sys.path.append('C:\Program Files (x86)\Keysight\SD1\Libraries\Python')

import keysightSD1
from keysightSD1 import SD_TriggerModes, SD_TriggerExternalSources, SD_TriggerBehaviors, SD_TriggerDirections
from keysightSD1 import SD_WaveformTypes, SD_Waveshapes, SD_MarkerModes, SD_SyncModes, SD_Error

import numpy as np
from scipy.interpolate import interp1d

class KeysightM3202A(Instrument):

    def __init__(self, name, slot, chassis=0):
        super().__init__(name, tags=['physical'])
        self.mask = 0
        self.module = keysightSD1.SD_AOU()
        self.module_id = self.module.openWithSlotCompatibility("M3202A", chassis, slot,
                                                               compatibility=keysightSD1.SD_Compatibility.LEGACY)
        self.clear()  # clear internal memory and AWG queues
        self.amplitudes = [0.0] * 4
        self.modulation_amplitudes = [0.0]*4
        self.offsets = [0.0] * 4

        # synchronize all AWG queue's with internal clock
        for channel in [1, 2, 3, 4]:
            self.module.AWGqueueSyncMode(channel - 1, syncMode=1)  # sync with internal CLKsys

        # Shamil a.k.a. 'BATYA' code here
        self.waveforms = [None] * 4
        self.waveform_ids = [-1] * 4
        self.waveshape_types = [SD_Waveshapes.AOU_AWG]*4  # in case of AM or FM
        self.repetition_frequencies = [None] * 4
        self.output_voltages = [None] * 4
        self.trigger_modes = [SD_TriggerModes.AUTOTRIG] * 4
        self.trigger_ext_sources = [SD_TriggerExternalSources.TRIGGER_EXTERN] * 4  # from front panel, can be also from PXI_n triggering bus
        self.trigger_behaviours = [SD_TriggerBehaviors.TRIGGER_RISE] * 4  # rising edge by default
        self.trigger_output = True
        self.synchronized_channels = []

        self._source_channels_group = []  # channels that are the source of the waveforms for dependent group
        self._dependent_channels_group = []  # channels which waveforms repeats corresponding waveforms from _source_channels_group
        self._update_dependent_on_start = False

    def _handle_error(self, ret_val):
        if( ret_val < 0 ):
            print(ret_val)
            raise Exception

    def clear(self):
        # clear internal memory and AWG queues
        ret = self.module.waveformFlush()
        self._handle_error(ret)

    def synchronize_channels(self, *channels):
        self.synchronized_channels = channels

    def unsynchronize_channels(self):
        self.synchronized_channels = []

    def set_trigger(self, trigger_string: str="CONT", channel: int=-1):
        """
        trigger_string : string
           'EXT' - external trigger on the front panel is used as a trigger signal source
           'OUT' - device trigger input is a source of the starting trigger
           'CONT' - continious output  <---- DEFAULT setting
        channel : int
            1,2,3,4 - channel number
        """
        if (channel == -1) or (channel in self.synchronized_channels):
            channels_to_config = self.synchronized_channels
        else:
            channels_to_config = [channel]

        for channel in channels_to_config:
            if trigger_string == "EXT":  # front panel
                # for each 'cycle' (see 'cycle' definition in docs)
                self.trigger_modes[channel-1] = SD_TriggerModes.EXTTRIG_CYCLE
                ret = self.module.AWGtriggerExternalConfig(channel-1, self.trigger_ext_sources[channel-1],  # front panel only
                                                           self.trigger_behaviours[channel-1],  # on rising edge by default
                                                           SD_SyncModes.SYNC_CLK10)  # sync with internal 100 MHz clock
                self._handle_error(ret)
            elif trigger_string == "CONT":
                self.trigger_modes[channel-1] = SD_TriggerModes.AUTOTRIG

    def trigger_output_config(self, trig_mode="ON", channel=-1, trig_length=1000):
        """
            Manipulates the output trigger.
            If channel is not supplied, trigger output is set for the first channel
        from synchronized channel group.
            To set synchronized channels, use self.synchronize_channels(...) method. If
        self.synchronized_channels were not set, then the default value is [] and no
        trigger output would be configured.

        Parameters
        ----------
        trig_mode : str
            "ON" - output trigger for 'channel' or the first channel in its synchronized group
            "OFF" - no output
        channel : int
            channel number to set trigger for
            if channel is in synchronized group than trigger is configured for the first channel in group
        trig_length : int
            trigger duration in ns
            trigger duration resolution is 10 ns
        """
        if trig_mode == "ON":
            self.trigger_output = True
        elif trig_mode == "OFF":
            self.trigger_output = False
        else:
            raise NotImplementedError("trig_mode argument can be only 'ON' or 'OFF' ")

        # if channel is equal to -1, then output trigger is disabled for all synchronized channels
        if (channel == - 1) or (channel in self.synchronized_channels):
            channels_to_config = self.synchronized_channels
        else:
            channels_to_config = [channel]

        for chan in channels_to_config:
            if self.trigger_output:  # enable trigger for the first channel from group
                # configuring trigger IO as output
                self.module.triggerIOconfig(SD_TriggerDirections.AOU_TRG_OUT)
                # here was changed to PXI trigger output
                # adding marker to the specified channel
                trgPXImask = 0b0
                trgIOmask = 0b1
                self.module.AWGqueueMarkerConfig(chan - 1, SD_MarkerModes.EVERY_CYCLE,
                                                 trgPXImask, trgIOmask, 1, syncMode=1,  # trigger sync with internal CLKsys
                                                 length=int(trig_length/10),  # trigger length (100a.u. x 10ns => 1000 ns trigger length)
                                                 delay=0)
                break  # first channel from group is enough to produce output marker
            else:  # disable trigger for all channels form group
                self.module.triggerIOconfig(SD_TriggerDirections.AOU_TRG_OUT)
                self.module.triggerIOwrite(0, SD_SyncModes.SYNC_NONE)  # make sure the output is zero
                # deleting marker to the specified channel
                trgIOmask = 0b1
                self.module.AWGqueueMarkerConfig(chan - 1, SD_MarkerModes.DISABLED,
                                                 0, 1, 1, syncMode=1,  # trigger synch with internal CLKsys
                                                 length=100,  # trigger length (100a.u. x 10ns => 1000 ns trigger length)
                                                 delay=0)
                self.module.triggerIOconfig(SD_TriggerDirections.AOU_TRG_IN)

    def output_arbitrary_waveform(self, waveform, frequency, channel, asynchronous=False):
        """
        Prepare and output an arbitrary waveform repeated at some repetition_rate

        Parameters:
        -----------
        waveform: array
            ADC levels, in Volts. max( abs(waveform) ) < 1.5 V
        repetition_rate: float, Hz
            frequency at which the waveform will be repeated
        channel: 1,2,3,4
            channel which will output the waveform

        NOTE_1: waveform length must be a multiple of 10, but you do not need to
        provide an array that complies with such condition. Note only that this array will be copied 10
        times before being loaded to board RAM in order to satisfy the datasheet condition above.
        See user guide, p.140
        and datasheet, p.8

        NOTE_2: Sampling speed depends on the SD_WaveformType value of the SD_Wave().
        see datasheet for more details

        NOTE_3: waveform's last point must coincide with the waveforms first point.
        Shamil: I assume there is no need to pass np.sin(2*np.pi*np.linspace(0,1,N_pts+1)) here
        because this array basically includes the boundary point twice, which values are already
        assumed to be equal to each other when specifying waveform frequency.
        It is rather more rational to  pass np.sin(2*np.pi*np.linspace(0,1,N_pts+1)[:-1])
        Also I'd prefer to pass function explicitly as lambda or something like that
        and only then generate points inside this class method. 25.04.2019

        NOTE_4: I suggest we use embeded function generators with amplitude modulation
        in our AWG solution, rather than generating sinus from scratch and using DAC only.
        This is necessary in order to improve frequency accuracy and stability.
        """
        # stopping AWG so the changes will take place according to the documentation
        # (not neccessary but a good practice)
        self.stop_AWG(channel)
        # loading a waveform to internal RAM and putting waveform into the channel's AWG queue
        self.load_waveform_to_channel(waveform, frequency, channel)
        # starting operation
        self.start_AWG(channel)

    def output_continuous_wave_old(self, frequency, amplitude, phase, offset, waveform_resolution,
                               channel, asynchronous=False):
        n_points = np.around(1 / frequency / waveform_resolution * 1e9) + 1 if frequency != 0 else 3
        waveform = amplitude * np.sin(2 * np.pi * np.linspace(0, 1, n_points) + phase) + offset
        self.output_arbitrary_waveform(waveform, frequency, channel, asynchronous=asynchronous)

    def output_continuous_wave(self, frequency, amplitude, phase, offset, waveform_resolution,
                               channel, asynchronous=False):
        self.stop_AWG(channel)
        self.stop_modulation(channel)
        self.setup_carrier_signal(frequency, amplitude, phase, offset, channel)

        # resetting phase for synchronization of multiple carrier signals
        self.module.channelPhaseResetMultiple(sum([1 << (chan - 1) for chan in self.synchronized_channels]))

        self.start_AWG(channel)

    def setup_carrier_signal(self, frequency, amplitude, phase, offset, channel):
        if frequency > 0:
            self.waveshape_types[channel - 1] = keysightSD1.SD_Waveshapes.AOU_SINUSOIDAL
        else:
            self.waveshape_types[channel - 1] = keysightSD1.SD_Waveshapes.AOU_DC
        self.module.channelWaveShape(channel-1, self.waveshape_types[channel - 1])
        self.output_voltages[channel - 1] = amplitude
        self.module.channelAmplitude(channel-1, self.output_voltages[channel-1])
        self.module.channelFrequency(channel-1, frequency)
        self.module.channelPhase(channel-1, phase/np.pi*180)
        self.module.channelOffset(channel-1, offset)

    @staticmethod
    def calc_sampling_rate(prescaler):
        if prescaler == 0:
            fs = int(1e9)
        elif prescaler == 1:
            fs = int(2e8)
        elif  prescaler > 1:
            fs = int(100//prescaler*1e6)
        return fs

    def setup_amplitude_modulation(self, channel, waveform_id, array, deviationGain, prescaler):
        self.load_modulating_waveform(array, waveform_id)
        self.queue_waveform(channel, waveform_id, prescaler)
        self.start_modulation_AM(channel, deviationGain)

    def load_modulating_waveform(self, waveform_array_normalized, wave_id):
        wave = keysightSD1.SD_Wave()
        wave.newFromArrayDouble(SD_WaveformTypes.WAVE_ANALOG, waveform_array_normalized)
        paddingMode = 0 # add zeros at the end if waveform length is smaller than
        ret = self.module.waveformLoad(wave, wave_id, paddingMode)
        if (ret == SD_Error.INVALID_OBJECTID):
            # probably, such wave_id already exists
            print("INVALID_OBJECTID")
            ret = self.module.waveformReLoad(wave, wave_id)
        self._handle_error(ret)

    def queue_waveform(self, channel, wave_id, prescaler):
        cycles = 0  # Zero specifies infinite cycles
        startDelay = 0
        ret = self.module.AWGqueueWaveform(channel - 1, wave_id, self.trigger_modes[channel - 1],
                                           startDelay, cycles, prescaler)
        self._handle_error(ret)

    def stop_modulation(self, channel):
        deviationGain = 0
        self.modulation_amplitudes[channel-1] = 0
        self.module.modulationAmplitudeConfig(channel - 1, keysightSD1.SD_ModulationTypes.AOU_MOD_OFF, deviationGain)

    def change_amplitude_of_carrier_signal(self, amplitude, channel, ampl_coef=1):
        self.output_voltages[channel - 1] = amplitude * ampl_coef
        self.module.channelAmplitude(channel - 1, self.output_voltages[channel - 1])

    def start_modulation_AM(self, channel, deviationGain):
        self.modulation_amplitudes[channel-1] = deviationGain
        self.module.modulationAmplitudeConfig(channel - 1, keysightSD1.SD_ModulationTypes.AOU_MOD_AM,
                                              deviationGain)

    def load_waveform_to_channel(self, waveform, frequency, channel, waveshape_type=None):
        waveform = np.array(waveform, dtype=np.float16, copy=True)

        if np.max(np.abs(waveform)) >= 1.5:
            raise Exception("signal maximal amplitude is exceeding AWG range: (-1.5 ; 1.5) volts")

        # number of points
        if (frequency > 1e9):
            raise Exception("frequency is exceeding AWG sampling rate: 1 GHz")

        duration_initial = 1 / frequency * 1e9 if frequency != 0 else 10.0  # float
        # interpolating input waveform to the next step
        # that rescales waveform to fit frequency
        interpolation_method = "cubic" if frequency != 0 else "linear"
        old_x = np.linspace(0, duration_initial, len(waveform))
        f_wave = interp1d(old_x, waveform, kind=interpolation_method)

        # in order to satisfy NOTE_1 we simply make 10 subsequent waveforms
        # but to provide frequency accuracy, we are sampling from
        # interval 1000 times wider then the original, and we are extending
        # interpolation function domain using its periodicity
        # duration = duration_initial*1e4 if duration_initial < 1e2 else 1e6  # here it is

        duration = duration_initial
        new_x = np.arange(0, duration, 1.0)

        # converting domain values in the function domain
        new_x_converted = np.remainder(new_x, duration_initial)
        waveform_array = f_wave(new_x_converted)  # obtaining new waveform walues

        normalization = np.max(np.abs(waveform_array))

        if (self.waveshape_types[channel-1] == SD_Waveshapes.AOU_AWG):
            self.output_voltages[channel-1] = normalization

        waveform_array /= normalization # normalize waveform to (-1,1) interval
        self.repetition_frequencies[channel - 1] = frequency

        self._load_array_into_AWG(waveform_array, channel, waveshape_type)

    def _load_array_into_AWG(self, waveform_array_normalized, channel, waveshape_type=None):
        from copy import deepcopy
        waveform_array_normalized = deepcopy(waveform_array_normalized)
        self.waveforms[channel - 1] = waveform_array_normalized
        self.waveform_ids[channel - 1] = channel

        # creating SD_Wave() object from keysight API
        wave = keysightSD1.SD_Wave()
        wave.newFromArrayDouble(SD_WaveformTypes.WAVE_ANALOG, waveform_array_normalized)
        wave_id = channel - 1

        # setting generation parameters
        # direct AWG, AM, offset modulation, FM, PHM, maybe more
        # see 'SD_Waveshapes' class for complete details
        if (waveshape_type is not None):
            self.waveshape_types[channel-1] = waveshape_type

        ret = self.module.channelWaveShape(channel - 1, self.waveshape_types[channel - 1])
        self._handle_error(ret)

        # load waveform to board RAM
        ret = self.module.waveformLoad(wave, wave_id)
        if (ret == SD_Error.INVALID_OBJECTID):
            # probably, such wave_id already exists
            ret = self.module.waveformReLoad(wave, wave_id)
        self._handle_error(ret)

        # clear channel queue
        self.module.AWGflush(channel-1)

        # put waveform as the first and only member of the
        # channel's AWG queue
        ret = self.module.AWGqueueWaveform(channel - 1, wave_id,
                                           self.trigger_modes[channel - 1],  # default trigger mode is "CONT"
                                           0,  # 0 ns starting delay
                                           0,  # 0 - means infinite
                                           0)  # prescaler is 0
        self._handle_error(ret)

        # set amplitude in volts
        ret = self.module.channelAmplitude(channel - 1, self.output_voltages[channel-1])
        self._handle_error(ret)

    def start_AWG(self, channel):
        if(self._update_dependent_on_start):
            self._load_dependent_channels()

        if ((not self.synchronized_channels) or (channel not in self.synchronized_channels)):
            ret = self.module.AWGstart(channel - 1)
            self._handle_error(ret)
        elif (channel in self.synchronized_channels):
            channels_mask = sum([1 << (chan - 1) for chan in self.synchronized_channels])
            ret = self.module.AWGstartMultiple(channels_mask)
            self._handle_error(ret)
        else:
            raise NotImplementedError("Check channel number conditions on argument provided: channel={}".format(channel))

        return ret

    def stop_AWG(self, channel):
        if (not self.synchronized_channels) or (channel not in self.synchronized_channels):
            ret = self.module.AWGstop(channel-1)
            self._handle_error(ret)
        elif channel in self.synchronized_channels:
            channels_mask = sum([1 << (chan - 1) for chan in self.synchronized_channels])
            ret = self.module.AWGstopMultiple(channels_mask)
            # print("{:b}".format(channels_mask))
            self._handle_error(ret)
        else:
            raise NotImplementedError("Check channel number conditions on argument provided: channel={}".format(channel))

    def setup_channel_duplicate_groups(self, _source_channels_group, _dependent_channels_group):
        """
        Set channels groups that are going to be duplicated
        Parameters
        ----------
        _source_channels_group : channels that are the source of the waveforms for dependent group
        _dependent_channels_group : channels which waveforms repeats corresponding waveforms from _source_channels_group

        Returns : None
        -------
        """
        self._source_channels_group = _source_channels_group
        self._dependent_channels_group = _dependent_channels_group
        self._update_dependent_on_start = True # copy source channels waveform to dependent channels during each call of self.start_AWG(...) method

    def reset_duplicate_groups(self):
        self._source_channels_group = []
        self._dependent_channels_group = []
        self._update_dependent_on_start = False

    def _load_dependent_channels(self):
        """
        Duplicate output from channels with numbers in self._source_channels_group to
        corresponding channels from self._dependent_channels_group
        Assuming len(group2) >= len(group1). Excessive channels from dependent group (group2) is not affected

        Parameters
        ----------
        group1 : list
        group2 : list

        Returns : None
        -------
        """
        for source_channel_idx, (source_chan, dependent_chan) in enumerate(zip(self._source_channels_group, self._dependent_channels_group)):
            if( self.waveforms[source_chan-1] is not None ):
                self.repetition_frequencies[dependent_chan-1] = self.repetition_frequencies[source_chan-1]
                self.output_voltages[dependent_chan-1] = self.output_voltages[source_chan-1]
                self._load_array_into_AWG(self.waveforms[source_chan-1], dependent_chan)
                # print(self.waveforms[source_chan-1]*self.output_voltages[source_chan-1],self.output_voltages[dependent_chan-1]*self.waveforms[source_chan-1])

    def plot_waveforms(self):
        import matplotlib.pyplot as plt
        plt.figure()
        for i, waveform in enumerate(self.waveforms):
            if(waveform is not None):
                plt.plot(waveform, label="CH"+str(i))
        plt.legend()