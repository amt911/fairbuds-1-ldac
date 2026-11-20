# Fairbuds LDAC

LDAC (and SBC-XQ) for the **Fairphone Fairbuds** (first generation, BES chip, firmware 6.5), installed
**over the air** from a Linux PC: no soldering, no UART/SWD, and you can go back to the official firmware
the same way.

> **Experimental.** This is a reverse-engineered patch to the official firmware, developed and tested on
> one pair of earbuds. It is not affiliated with or endorsed by Fairphone. Use it at your own risk.

## What you get

`tools/build_all.sh` builds three images from the official Fairbuds app:

| Image | What it is |
|---|---|
| `patched/stock_6_5.bin` | The untouched official firmware 6.5 (to go back to stock) |
| `patched/sbcxq_6_5.bin` | Official firmware with the SBC bitpool limit raised from 39 to 53 ("SBC-XQ"), nothing else |
| `patched/ldac/fairbuds_6_5_ldac.bin` | SBC-XQ plus an **LDAC** decoder: 44.1 / 48 kHz, stereo, both earbuds (TWS) |

Status of the LDAC build:

- Works with a Samsung Galaxy S25 Ultra (pick LDAC by hand, Samsung defaults to AAC) and Linux PipeWire.
- Decoding was verified on the earbuds with built-in statistics: no decoding errors, no underflows and no
  restarts over full songs, with the CPU at ~22 % load.
- Known differences from the official codecs:
  - the sound balance can differ from AAC. The earbuds apply the same EQ preset to every codec (checked
    in the firmware). The difference comes from the source: LDAC at 660/990 kbps keeps treble that phone
    AAC encoders cut around 16–18 kHz, LDAC at 330 kbps cuts more, and phones process each codec
    differently (Samsung's UHQ upscaler, Dolby Atmos, 44.1 vs 48 kHz). For a fair comparison, fix the
    phone's LDAC quality at 990 kbps;
  - **higher battery use while LDAC plays** (the CPU runs at ~208 MHz instead of ~104 MHz);
  - **~320 ms of buffering**, so video may lag on players that do not compensate.
- Not supported: 88.2/96 kHz and mono LDAC. The earbuds only advertise 44.1/48 kHz stereo.
- On a PC, another Bluetooth device on the same adapter (a mouse, for example) can starve the LDAC
  stream (`Missing completion reports` in the PipeWire log) and cause dropouts. That is a PC-side problem.

## Safety

- **Charge both earbuds above 50 % before flashing.** An update at 20 % battery made an earbud reset
  while it was writing flash. Nothing was applied and it kept the old firmware, but do not push your luck.
- Keep **both earbuds out of the case** and connected to each other. Over TWS, the earbuds only answer
  the OTA when both are present.
- Turn your phone's Bluetooth off while flashing from the PC.
- Every image is first checked with the same test the earbuds run before accepting an update
  (`tools/besfw.py check`). The flasher only sends the final "commit" command after the earbuds
  report a valid image, so an interrupted transfer leaves the old firmware in place. If a transfer fails
  (`RX 8300`, timeouts), just run it again.
- Do your first LDAC listening test **at low volume with the earbuds out of your ears**. Early
  development builds produced loud clicks.

## Requirements

- Linux with BlueZ and a Bluetooth adapter, with the earbuds paired (`bluetoothctl`, pair "Fairbuds").
- `python3`, `unzip`, `curl`, `make`, `clang` and `ld.lld` (LLVM). No ARM GCC is needed.
- The **official Fairbuds Android app 5.3.0**, which ships firmware 6.5. Pull it from your phone
  (`adb shell pm path` with the app's package, then `adb pull`) or download it from an APK mirror.
  The build checks the SHA-256 of the firmware inside the APK and refuses anything else.

Nothing from Fairphone or Sony is stored in this repository. The build extracts the firmware from your APK
and downloads Sony's LDAC decoder library (`ldac_lib.a`) and header from the public
[OpenPineBuds](https://github.com/pine64/OpenPineBuds) SDK, at a pinned commit and checked by SHA-256.

## Build

```sh
tools/build_all.sh /path/to/Fairbuds-5.3.0.apk
```

## Flash

1. Charge the earbuds, take **both** out of the case and connect them to the PC.
2. Flash. The address of the paired "Fairbuds" is found automatically; pass `--addr` if you have more
   than one pair.
   ```sh
   python3 tools/pc_ota3.py patched/ldac/fairbuds_6_5_ldac.bin
   ```
   The transfer takes about a minute and must end with `COMMIT OK`. The earbuds then restart with the
   new firmware. A log `pc_ota3_*.log` is written to the current directory.
3. Select LDAC:
   - **Android**: Settings → Developer options → *Bluetooth audio codec* → **LDAC**. Samsung phones start
     on AAC, so switch them by hand.
   - **Linux (PipeWire)**: choose the profile *High Fidelity Playback (A2DP Sink, codec LDAC)*. Right after
     a reconnect, if there is no LDAC stream, switch to another codec and back.
4. **No music after an update** but voice prompts work? Open the Fairbuds app and pick an EQ preset.
   An update can leave the custom EQ in a silent state; this also happens with the official firmware.

### Going back

Flash `patched/stock_6_5.bin` (official) or `patched/sbcxq_6_5.bin` (official plus SBC-XQ) the
same way. After flashing, BlueZ may keep showing the old codec list until you disconnect and reconnect.

## Diagnostics

The LDAC build answers a private command on the update channel with decoder statistics: load, underflows,
late DMA periods, buffer level, CPU speed. Read them while music plays:

```sh
python3 tools/ldac_stats.py            # one line per second; Ctrl-C to stop
```

For listening tests of a new build, `tools/guarded_play.py SINK FILE` plays a file and stops it at
the first anomaly in those statistics, or if the statistics stop arriving.

## How it works

The official firmware is left as it is. A 26 KB add-on is appended after it, and six instructions are
redirected to it (`tools/build_ldac_image.py` checks the original bytes at each site):

| Hook | Site | Purpose |
|---|---|---|
| H1 | A2DP init | register an LDAC stream endpoint (vendor codec, Sony `0x012D`/`0x00AA`) |
| H2, H5 | stream configuration / open | mark the stream as LDAC (sample rate, 16-bit, stereo) |
| H3 | decoder setup | swap the SBC decoder table for the LDAC one |
| H4 | earbud-initiated configuration | answer with an LDAC configuration |
| H6 | OTA command dispatcher | statistics command `0xA5` (all other commands untouched) |

The decoder (`ldac/src/ldac_decoder.c`) plugs Sony's library into the firmware's A2DP decoder
framework. Its behaviour copies the firmware's own SBC decoder where the framework depends on it. For
example, the TWS secondary earbud aligns itself by dropping whole packets.

A few things the official firmware does not do for an unknown codec, so the add-on does them itself:

- it requests the top CPU clock with its own `sysfreq` user (the firmware resets the A2DP one);
- it sets the playback-speed tuner's buffer target (the firmware passes 0 and drains the buffer);
- it runs the LDAC library from a relocated copy in RAM (`tools/mkpayload.py`).

The lab notes, in Spanish, are in `docs/FINDINGS.md` and `docs/LDAC_PORT.md`.

## Development and tests

The tests run the real ARM code of the patched image in Unicorn.

- `ldac/test/emu_decoder.py`: the decoder fed with LDAC encoded on the host by AOSP `libldacBT_enc`,
  compared sample by sample.
- `ldac/test/emu_hooks.py`: the six patched sites.

Each test has a sabotage script that breaks the build on purpose and must make the test fail.

```sh
python3 -m venv .venv && .venv/bin/pip install unicorn capstone
make -C ldac addon
python3 tools/build_ldac_image.py --blob ldac/build/addon.elf --out /tmp/ldac.bin
.venv/bin/python ldac/test/emu_hooks.py   /tmp/ldac.bin ldac/build/addon.elf
.venv/bin/python ldac/test/emu_decoder.py /tmp/ldac.bin ldac/build/addon.elf   # needs libldacBT_enc.so
.venv/bin/python ldac/test/sabotage_hooks.py /tmp/ldac.bin ldac/build/addon.elf
ldac/test/sabotage_decoder.sh
```

Reverse-engineering helpers (Ghidra headless) are in `tools/{mkelf,decomp,xrefs}.sh` and
`ghidra_scripts/`.

## Credits and legal

- LDAC is a trademark of Sony Corporation. The LDAC decoder library is Sony's. It is not included here,
  and you are responsible for complying with its terms.
- Fairbuds and the Fairbuds firmware are Fairphone's. The firmware is not included here.
- Thanks to the [OpenPineBuds](https://github.com/pine64/OpenPineBuds) project, whose SDK documents the
  BES platform.
- The code in this repository is MIT-licensed (see `LICENSE`) and comes with no warranty.
