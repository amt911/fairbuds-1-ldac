# Fairbuds LDAC

LDAC (and SBC-XQ) for the **Fairphone Fairbuds** (first generation, BES chip, firmware 6.5), installed
**over the air** from a Linux PC: no soldering, no UART/SWD, and you can go back to the factory firmware
the same way.

> **Experimental.** This is a reverse-engineered patch to the official firmware, developed and tested on
> one pair of earbuds. It is not affiliated with or endorsed by Fairphone. Use it at your own risk.

The tested version is the git tag **[`v7j`](https://github.com/amt911/fairbuds-decomp/releases/tag/v7j)**.

## What you get

`tools/build_all.sh` builds three firmware images from the official Fairbuds app:

| Image | What it is |
|---|---|
| `patched/stock_6_5.bin` | The **factory firmware** 6.5, untouched (to go back to stock) |
| `patched/sbcxq_6_5.bin` | Factory firmware with the SBC bitpool limit raised from 39 to 53 ("SBC-XQ"), nothing else |
| `patched/ldac/fairbuds_6_5_ldac.bin` | SBC-XQ plus an **LDAC** decoder: 44.1 / 48 kHz, stereo, both earbuds (TWS) |

### Status of the LDAC build

- **It works** with a Samsung Galaxy S25 Ultra and with Linux PipeWire, at 44.1 and 48 kHz. That
  includes switching the rate from Android's developer options while music plays: expect a one-second
  gap while the earbuds restart the player at the new rate.
- Verified on the earbuds with built-in statistics: over full songs there were no decoding errors, no
  buffer underflows and no restarts, with the CPU at ~22 % load.
- Known differences from the official codecs:
  - **The sound balance can differ from AAC.** The earbuds apply your EQ preset to every codec in the
    same way (checked in the firmware). The difference comes from the source:
    - LDAC at 660/990 kbps keeps treble that phone AAC encoders cut around 16–18 kHz;
    - LDAC at 330 kbps cuts more;
    - phones process each codec differently (Samsung's UHQ upscaler, Dolby Atmos, 44.1 vs 48 kHz).

    For a fair comparison, set the phone's LDAC quality to 990 kbps.
  - **Higher battery use while LDAC plays.** The CPU runs at ~208 MHz instead of ~104 MHz. SBC and AAC
    are not affected.
  - **~320 ms of buffering**, so video may lag on players that do not compensate.
- **Not supported:** 88.2/96 kHz and mono LDAC. The earbuds only advertise 44.1/48 kHz stereo (see the
  [FAQ](#faq)).

## Quick start

On a Linux PC with Bluetooth:

```sh
git clone https://github.com/amt911/fairbuds-decomp.git && cd fairbuds-decomp
git checkout v7j                                   # the tested version
tools/build_all.sh /path/to/Fairbuds-5.3.0.apk    # see "The official Fairbuds app" below
python3 tools/pc_ota3.py patched/ldac/fairbuds_6_5_ldac.bin
```

Then select LDAC on your phone. Read [Safety](#safety) first; each step is explained below.

## Safety

- **Charge both earbuds above 50 % before flashing.** An update at 20 % battery made an earbud reset
  while it was writing flash. Nothing was applied and it kept the old firmware, but do not push your luck.
- Keep **both earbuds out of the case** and paired to each other. Over TWS, the earbuds only answer the
  update when both are present.
- **Turn your phone's Bluetooth off while flashing from the PC.** The earbuds accept one connection at
  a time.
- Each image is checked with the same test the earbuds run before accepting an update
  (`tools/besfw.py check`). The flasher only sends the final "commit" command after the earbuds report
  a valid image. An interrupted transfer therefore leaves the old firmware in place: just run it again.
- Do your first LDAC listening test **at low volume, with the earbuds out of your ears**. Early
  development builds produced loud clicks.

## What you need

### A Linux PC

You need Bluetooth (BlueZ), Python 3, `unzip`, `curl`, `make`, and `clang` + `ld.lld` (LLVM). No ARM GCC
toolchain is needed.

```sh
sudo pacman -S --needed bluez bluez-utils python clang lld make unzip curl   # Arch
sudo apt install bluez python3 clang lld make unzip curl                     # Debian / Ubuntu
```

Pair the earbuds once:

```sh
bluetoothctl
  power on
  scan on            # wait until "Fairbuds" appears, note its address
  pair XX:XX:XX:XX:XX:XX
  trust XX:XX:XX:XX:XX:XX
  connect XX:XX:XX:XX:XX:XX
```

### The official Fairbuds app, version 5.3.0

Nothing from Fairphone or Sony is stored in this repository. The build takes the **factory firmware
6.5** from the official Android app, where it ships as `res/raw/fw_fairbuds_6_5.bin`.

- Package `com.fairphone.android.fairbudsapp`, version **5.3.0** (version code 50300).
- **From your phone**, if version 5.3.0 is installed. Enable USB debugging, connect the phone and run:

  ```sh
  adb shell dumpsys package com.fairphone.android.fairbudsapp | grep versionName   # must say 5.3.0
  adb shell pm path com.fairphone.android.fairbudsapp     # prints package:/data/app/.../base.apk
  adb pull /data/app/.../base.apk Fairbuds-5.3.0.apk       # the base.apk path printed above
  ```

- **From an APK mirror** (APKMirror, APKPure…): download version 5.3.0 of "Fairbuds" by Fairphone.
  The build does not trust the APK. It checks the SHA-256 of the firmware inside and refuses anything
  else, so a modified APK or a different version is rejected rather than flashed.

Later versions of the app may ship a different firmware. This patch is made for firmware 6.5 and was only
tested on earbuds that were already running 6.5. The Fairbuds app shows the firmware version of your
earbuds.

### The factory firmware on its own

`tools/build_all.sh` saves it as `patched/stock_6_5.bin`. To extract it by hand:

```sh
unzip -p Fairbuds-5.3.0.apk res/raw/fw_fairbuds_6_5.bin > fw_fairbuds_6_5.bin
sha256sum fw_fairbuds_6_5.bin
# 97e96e2b6ef953b7d27da6952515e6356e775e60e7487338ffd66e1e702c89a1
```

Flashing this file puts the earbuds back on the official firmware (see [Going back](#going-back-to-the-factory-firmware)).

### Sony's LDAC decoder library

The build downloads `ldac_lib.a` and `ldacBT.h` from the public
[OpenPineBuds](https://github.com/pine64/OpenPineBuds) SDK, at a pinned commit, and checks both by
SHA-256. Without internet access on the build machine, download them elsewhere and put them in
`ldac/lib/ldac_lib.a` and `ldac/inc/ldacBT.h`:

```
https://raw.githubusercontent.com/pine64/OpenPineBuds/81b9afc71ac7e5d41a10ff9229279e645bbc1175/thirdparty/audio_codec_lib/ldac/lib/ldac_lib.a
https://raw.githubusercontent.com/pine64/OpenPineBuds/81b9afc71ac7e5d41a10ff9229279e645bbc1175/thirdparty/audio_codec_lib/ldac/inc/ldacBT.h
```

## Build

```sh
tools/build_all.sh /path/to/Fairbuds-5.3.0.apk
```

The build takes a few seconds and finishes with
`device would answer 0x84 01` for each of the three images, which means each one passes the earbuds'
own acceptance check. The images are written to `patched/`.

## Flash

1. Charge the earbuds, take **both** out of the case, turn the phone's Bluetooth off and connect the
   earbuds to the PC.
2. Flash. The address of the paired "Fairbuds" is found automatically; pass `--addr XX:XX:XX:XX:XX:XX`
   if you have more than one pair.

   ```sh
   python3 tools/pc_ota3.py patched/ldac/fairbuds_6_5_ldac.bin
   ```

   The transfer takes about a minute and must end with `COMMIT OK`. The earbuds then restart with the
   new firmware. A log `pc_ota3_*.log` is written to the current directory.
3. Select LDAC:
   - **Android**: Settings → Developer options → *Bluetooth audio codec* → **LDAC**. Samsung phones start
     on AAC, so switch them by hand. *LDAC playback quality* sets the bitrate (990 kbps is best).
   - **Linux (PipeWire)**: choose the profile *High Fidelity Playback (A2DP Sink, codec LDAC)*.
4. **No music after an update**, but voice prompts work? Open the Fairbuds app and pick an EQ preset.
   An update can leave the custom EQ in a silent state; this also happens with the official firmware.

### Going back to the factory firmware

Flash the factory image the same way, or the SBC-XQ image if you want to keep the better SBC:

```sh
python3 tools/pc_ota3.py patched/stock_6_5.bin     # factory firmware 6.5
python3 tools/pc_ota3.py patched/sbcxq_6_5.bin     # factory firmware + SBC-XQ
```

After flashing, BlueZ may keep showing the old codec list until you disconnect and reconnect.

## Troubleshooting

| Symptom | What to do |
|---|---|
| `probe failed` / timeout before the transfer starts | Both earbuds out of the case and charged; the phone's Bluetooth off; reconnect the earbuds to the PC. |
| `RX 8300` or a timeout during the transfer | Nothing was applied. Run the same command again. |
| The flasher finds 0 or 2+ "Fairbuds" | Pass `--addr XX:XX:XX:XX:XX:XX`. |
| No LDAC option on the phone after flashing | Forget the earbuds on the phone and pair again, so it reads the new codec list. |
| No music, only voice prompts | Pick an EQ preset in the Fairbuds app. |
| Dropouts with a PC | Another Bluetooth device on the same adapter (a mouse, for example) can starve the stream (`Missing completion reports` in the PipeWire log). Turn it off. |
| No LDAC stream on Linux right after reconnecting | Switch to another codec profile and back. |

## Diagnostics

The LDAC build answers a private command on the update channel with decoder statistics: load, underflows,
late DMA periods, buffer level and CPU speed. Read them while music plays from the PC:

```sh
python3 tools/ldac_stats.py            # one line per second; Ctrl-C to stop
```

`python3 tools/ldac_stats.py --debug` prints a "black box" that survives phone sessions. The PC cannot
watch while a phone plays, because the earbuds accept one connection at a time. The black box holds:

- the last codec configuration events, with their raw bytes;
- the decoder's sample rate in the last two streams;
- how the last stream went: frames, bad frames, underflows and the header of the first bad frame.

To use it, play from the phone, turn the phone's Bluetooth off, connect the PC and run the command.

For listening tests of a new build, `tools/guarded_play.py SINK FILE` plays a file and stops it at the
first anomaly in those statistics, or if the statistics stop arriving.

## FAQ

**Why not 96 kHz?** The decoder handles it: in emulation it decodes correctly with ~1.7× the work, about
36 % of the CPU (`ldac/test/emu_rates.py`). It is not advertised for three reasons:

- the factory player only knows sample rates up to 48 kHz;
- LDAC uses the same bitrates at 96 kHz as at 48 kHz, so it would spend bits on frequencies earbuds
  cannot play;
- phones usually pick the highest rate offered by default, so everyone would get the worse setting.

**Does it change anything for SBC or AAC?** AAC is untouched. SBC gets the higher bitpool limit
(SBC-XQ), which phones and PCs use when they support it.

**Which earbuds?** Fairphone Fairbuds, first generation. The Fairbuds XL use different firmware and are
not supported.

## How it works

The official firmware is left as it is. A 27 KB add-on is appended after it, and six instructions are
redirected to it (`tools/build_ldac_image.py` checks the original bytes at each site):

| Hook | Site | Purpose |
|---|---|---|
| H1 | A2DP init | register an LDAC stream endpoint (vendor codec, Sony `0x012D`/`0x00AA`) |
| H2, H5 | stream reconfiguration / open | mark the stream as LDAC (sample rate, 16-bit, stereo) |
| H3 | decoder setup | swap the SBC decoder table for the LDAC one |
| H4 | earbud-initiated configuration | answer with an LDAC configuration |
| H6 | update command dispatcher | statistics commands (all other commands untouched) |

The decoder (`ldac/src/ldac_decoder.c`) plugs Sony's library into the firmware's A2DP decoder
framework. Where the framework depends on the decoder's behaviour, it copies the firmware's own SBC
decoder. For example, the TWS secondary earbud aligns itself by dropping whole packets.

The official firmware does not do a few things for an unknown codec, so the add-on does them itself:

- it requests the top CPU clock with its own `sysfreq` user (the firmware resets the A2DP one);
- it sets the playback-speed tuner's buffer target (the firmware passes 0 and drains the buffer);
- it runs the LDAC library from a relocated copy in RAM (`tools/mkpayload.py`);
- it handles codec events that arrive with an empty codec description. The firmware's Bluetooth stack
  does not copy vendor codec details on an AVDTP reconfigure (a phone changing the sample rate), nor on
  the open that follows a configuration started by the earbuds. The add-on treats such an event as LDAC
  at the last known rate. If the frame headers then show another rate, it fixes the rate and restarts
  the player, at most 3 times in a row.

The lab notes, in Spanish, are in `docs/FINDINGS.md` and `docs/LDAC_PORT.md`.

## Repository layout

| Path | Contents |
|---|---|
| `tools/` | Build (`build_all.sh`, `build_ldac_image.py`, `mkpayload.py`), flasher (`pc_ota3.py`, `besfw.py`), diagnostics (`ldac_stats.py`, `guarded_play.py`), Ghidra helpers |
| `ldac/` | Add-on sources (`src/`), linker scripts, firmware symbols (`stock_syms.ld`), tests (`test/`) |
| `ghidra_scripts/` | Headless Ghidra scripts used for the reverse engineering |
| `docs/` | Lab notes (Spanish) |

## Development and tests

The tests run the real ARM code of the patched image in Unicorn:

- `ldac/test/emu_decoder.py` feeds the decoder LDAC encoded on the host with AOSP's `libldacBT_enc`
  (package `libldac` on Arch, `libldacbt-enc2` on Debian/Ubuntu) and compares the output sample by
  sample;
- `ldac/test/emu_hooks.py` exercises the six patched sites.

Each test has a sabotage script that breaks the build on purpose and must make the test fail.

```sh
python3 -m venv .venv && .venv/bin/pip install unicorn capstone
make -C ldac addon
python3 tools/build_ldac_image.py --blob ldac/build/addon.elf --out /tmp/ldac.bin
.venv/bin/python ldac/test/emu_hooks.py      /tmp/ldac.bin ldac/build/addon.elf
.venv/bin/python ldac/test/emu_decoder.py    /tmp/ldac.bin ldac/build/addon.elf
.venv/bin/python ldac/test/sabotage_hooks.py /tmp/ldac.bin ldac/build/addon.elf
ldac/test/sabotage_decoder.sh
```

## Credits and legal

- LDAC is a trademark of Sony Corporation. The LDAC decoder library is Sony's. It is not included here,
  and you are responsible for complying with its terms.
- Fairbuds and the Fairbuds firmware are Fairphone's. The firmware is not included here.
- Thanks to the [OpenPineBuds](https://github.com/pine64/OpenPineBuds) project, whose SDK documents the
  BES platform.
- The code in this repository is MIT-licensed (see `LICENSE`) and comes with no warranty.
