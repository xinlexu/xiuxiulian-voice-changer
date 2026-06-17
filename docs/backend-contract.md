# Backend Contract

`羞羞脸变声器` starts as a clean-room control panel for a local RVC runtime already present on the user's Windows machine.

Voice root discovery order:

```text
VOICE_ROOT environment variable
Saved path in %APPDATA%\xiuxiulian-voice-changer\settings.json
<project>\VoiceChanger
<project parent>\VoiceChanger
D:\VoiceChanger
C:\VoiceChanger
E:\VoiceChanger
F:\VoiceChanger
```

Controller command:

```powershell
<voice-root>\runtime\python.exe -c "from rvc_core._ctl import main; main()"
```

Observed local API:

```text
GET  http://127.0.0.1:5000/status
GET  http://127.0.0.1:5000/devices
POST http://127.0.0.1:5000/start
POST http://127.0.0.1:5000/stop
POST http://127.0.0.1:5000/update
```

Observed `/status` response:

```json
{
  "running": false,
  "latency": 0.0
}
```

Observed `/devices` response:

```json
{
  "hostapis": ["MME", "Windows DirectSound", "Windows WASAPI", "Windows WDM-KS"],
  "input_devices": ["..."],
  "output_devices": ["..."]
}
```

Local config shape:

```json
{
  "audio": {
    "sr_type": "sr_device",
    "block_time": 0.42,
    "crossfade_time": 0.05,
    "extra_time": 0.45,
    "threshold": -50,
    "rms_mix_rate": 0,
    "input_noise_reduce": false,
    "output_noise_reduce": false,
    "use_phase_vocoder": false
  },
  "model": {
    "pth_path": "5.3(1).pth",
    "index_path": "",
    "pitch": 5,
    "formant": 0.3,
    "index_rate": 0,
    "f0_method": "fcpe",
    "n_cpu": 4
  },
  "device": {
    "hostapi": "Windows WASAPI",
    "input_device": "Yeti (Yeti Classic)",
    "output_device": "伴侣扬声器 (VB-Audio Cable A)",
    "wasapi_exclusive": false
  }
}
```

Scope:

- No membership, login, remote auth, or premium gate.
- No copied proprietary frontend assets.
- No reverse engineering of proprietary implementation internals.
- First milestone controls the local runtime through documented runtime behavior observed on this machine.
