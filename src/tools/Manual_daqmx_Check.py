from nidaqmx import Task
from nidaqmx.constants import AcquisitionType
t = Task()
t.ai_channels.add_ai_voltage_chan("AGENTMod1/ai0", min_val=0.0, max_val=10.0)
t.timing.cfg_samp_clk_timing(rate=100.0, sample_mode=AcquisitionType.CONTINUOUS, samps_per_chan=100)
t.start()
print("started")
data = t.read(number_of_samples_per_channel=10, timeout=0.2)  # 0.2s to give headroom
print("read ok, samples:", len(data) if isinstance(data, list) else "1-chan")
t.close()