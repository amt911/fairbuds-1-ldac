# Fairbuds 1 — notas de ingeniería inversa

Fecha: 2026-11-19
Objetivo: habilitar LHDC primero, LDAC después, todo por OTA, sin UART.

## Dispositivo / firmware

- APK: `Fairbuds_5.3.0_APKPure.apk` (Fairphone Fairbuds app).
- Firmware Fairbuds 1: `res/raw/fw_fairbuds_6_5.bin`
  - SHA-256: `97e96e2b6ef953b7d27da6952515e6356e775e60e7487338ffd66e1e702c89a1`
  - Tamaño: 1.667.204 bytes.
  - Chip: **BES1501P / BES2300** (doble Cortex-M4F, 300 MHz, 992 KB SRAM, 4 MB flash).
  - Build: `Jun 22 2024 15:04:25`, RTX5.
  - Base de carga: **0x2C020000**.
  - Trailer: `CRC32_OF_IMAGE=0x1F23201A`; coincide con `crc32(imagen[:len-0x60])`.
  - Imagen **sin cifrar** (Thumb-2). Las XL son Qualcomm QCC512x (`APPUHDR5`); los Fairbuds 2 son otra imagen cifrada/comprimida.

## Codecs observados en el dispositivo

Consulta AVDTP directa (no caché de BlueZ), MAC de los cascos:

- SEID 1: **SBC**, caps `3F FF 02 27`.
- SEID 2: **AAC**, caps `80 01 8C 82 00 00`.
- No hay SEP de LHDC, LDAC ni aptX.

## LHDC en el firmware

- Función de detección LHDC en `0x2C0C0734`:
  - vendor ID `0x0000053A`
  - codec ID `0x4C32` ("L2") -> LHDC
  - codec ID `0x4C35` ("L5") -> LHDC V5
  - devuelve 1 / 2.
- Strings: `USE codec type is LHDC.` @ `0x2C1631D4`, `USE codec type is LHDCV5.` @ `0x2C1631EC`.
- Tabla de vendor codecs en file offset `0x14957C` (cerca de `a2dp_register_sep`):
  - LHDC `0x53A` / `"L5"`
  - Sony `0x12D`
  - Samsung `0x75`

### Corrección importante: LHDC es un stub, no un codec funcional

- La función de detección se llama **una sola vez**, desde `0x2C0C0822`,
  y **su valor de retorno se ignora** (`mov r0, r6` inmediatamente después).
- El init de A2DP (`0x2C0BF8F4`) solo registra **SBC** (`0x2C0C2894`) y
  **AAC** (`0x2C0C2858`). No hay registro de LHDC.
- No hay decoder LHDC enlazado en la imagen de la app.
- Conclusión: LHDC no es un flag que se pueda activar; habría que portar o
  enlazar un decoder.



### Registro de SEPs A2DP

- Init A2DP en `0x2C0BF8F4`:
  - `bl 0x2C0C2894` -> setup **SBC** (caps `0x2C164238`, `strh 0x0400`).
  - `bl 0x2C0C2858` -> setup **AAC** (caps `0x2C164230`, `strh 0x0602`).
  - **No hay llamada a setup LHDC.**
- Ambas llaman a `0x2C103444` con un descriptor:
  - `+0x00`: uint16 tipo/flags (`0x0400` SBC, `0x0602` AAC)
  - `+0x02`: puntero a caps
  - `+0x0A`: byte 1
- Para habilitar LHDC habría que añadir una tercera llamada con descriptor LHDC
  (caps propias + tipo) **si el decoder LHDC está realmente en la imagen**.
  Pendiente: confirmar presencia del decoder (la sección de licencia no está
  listada en el trailer de build).

## LDAC

- Solo aparece en un log genérico: `(d%x) codec type=%d/LHDC or LDAC` @ `0x2C1709B4`.
- No hay decoder LDAC ni vendor ID Sony en las capacidades A2DP.
- Conclusión: LDAC requeriría portar decoder + añadir SEP; LHDC es el camino corto.

## Alternativas realistas

### SBC-XQ (parche de 1 byte)

- Caps SBC en `0x2C164238`: `3F FF 02 27`
  - `+0`: 0x3F (freq + canales)
  - `+1`: 0xFF (bloques/subbandas/asignación)
  - `+2`: 0x02 (bitpool mínimo)
  - `+3`: 0x27 = 39 (bitpool máximo)
- SBC-XQ usa bitpool máximo 53. Parche: `0x2C16423B` (file `0x14423B`)
  de `0x27` a `0x35`.
- Riesgo bajo, reversible, y mejora real de calidad. Buen primer test del
  pipeline OTA completo.

### LE Audio / LC3

- Build `best1501p_ibrt_anc_leaudio`; strings `LE_AUDIO`, `BIS_T`,
  `app_audio_trigger_toggle_a2dp_cis`.
- Pero `warning! not support leaudio` y **no hay strings LC3**.
- Pendiente: confirmar si el codec LC3 está enlazado y si el flag se puede
  activar. Sería la mejora grande más plausible.

## LDAC

## OTA (BES TOTA sobre BLE)

UUIDs (de `TWSServiceManager.java`):

- Servicio OTA: `66666666-6666-6666-6666-666666666666`
- Característica OTA: `77777777-7777-7777-7777-777777777777`
- Servicio QXW: `0000ff12-0000-1000-8000-00805f9b34fb`
- Write QXW: `0000ff14-...`
- Notify QXW: `0000ff13-...`

Framing TOTA: `[opcode] [len] "BEST" [payload]`
Opcode PC -> dispositivo:

- `0x8C` handshake (random code + CRC32)
- `0x86` config (nombre "Fairbuds", MAC, ...)
- `0x80` info de imagen (tamaño + CRC32)
- `0x85` datos
- `0x82` CRC de segmento
- `0x88` fin
- `0x8E` "FW request" (`getOtaVersionRequest` = `8E 42 45 53 54`)

Respuestas dispositivo -> PC: `0x8F`, `0x8D`, `0x83`, `0x84`, `0x81`, `0x87`, etc.
CRC: `java.util.zip.CRC32`, little-endian.

Implementación decompilada:

- `work/jadx/sources/s9/d.java` — packet builder.
- `work/jadx/sources/s9/e.java` — máquina de estados.
- `work/jadx/sources/s9/a.java` — CRC y helpers.
- `work/jadx/sources/com/fairphone/fairsound/core/bluetooth_api/bluetooth/protocol/tws/TWSCommand.java` — QXW.
- `work/jadx/sources/com/fairphone/fairsound/core/bluetooth_api/bluetooth/protocol/tws/TWSServiceManager.java` — UUIDs y flujo.

## Lectura de versión (solo lectura, cero riesgo)

QXW `getFirmwareRequest()`:

```
51 58 57 27 01 01 02
```

Escribir en `0000ff14-...` y leer la notificación de `0000ff13-...`.
Respuesta de firmware empieza por `0x8F` + `"BEST"`; versión en bytes 6..14.

## Seguridad / boot info

- `app_update_ota_boot_info`, `ota_common_get_boot_info_flash_offset`.
- Magic `0x5A5A5A5A`; dos registros de boot info (`0x20081000` / `0x20000800`).
- Variables: `dstFlashOffsetForNewImage`, `offsetInFlashToProgram`, `totalImageSize`.

### Mecanismo de escritura (confirmado en desensamblado)

En `0x2C0AD4F2`..`0x2C0AD55E`:

- `env` = `0x2008A844`.
- `env+0xB1` = ID de partición destino.
- `env+0xB2` = número de entradas de la tabla de particiones.
- Tabla en `env+0xB4`, entradas de **24 bytes**:
  - `+0xB4`: ID de partición (byte)
  - `+0xB8`: offset de la partición (32 bits; se enmascara con `0x00FFFFFF`)
- El código busca la entrada cuyo ID coincide con `env+0xB1` y hace:
  - `env+0x4` (`dstFlashOffsetForNewImage`) = offset de esa partición.
  - `env+0xC` = 0, `env+0x18` = 0.
- Tras escribir y verificar, `0x2C0AD184`..`0x2C0AD1AC`:
  - `env+0x10` = `totalImageSize`
  - `env+0x20` = `crc32`
  - `env+0x28` = 1
  - `*boot_info = 3`
  - llama a `app_update_ota_boot_info` (`0x2C0AC5C0`), que escribe tamaño/CRC
    en el boot info (`flash offset 0x20` y `0x24`).

**Conclusión:** el OTA escribe en una partición distinta (staging por tabla de
particiones) y solo actualiza el boot info después. No es in-place. El riesgo
residual es un corte de corriente durante la escritura del boot info, no durante
la imagen. Aun así, hay que probar el reflash de la stock antes de tocar nada.

## Siguiente paso

1. Cliente BLE de solo lectura: consultar versión actual del dispositivo (QXW).
2. Terminar de fijar `dstFlashOffsetForNewImage` y el rollback en Ghidra.
3. Cliente TOTA; probar reflash de la stock 6.5 (recuperación probada).
4. Parche mínimo: añadir/activar SEP LHDC; flashear y validar.

## SDK OpenPineBuds (PineBuds Pro, mismo BES2300)

Clonado en `/tmp/opencode/OpenPineBuds` (106 MB). **Compila** con la imagen
Docker `ghcr.io/pine64/openpinebuds:latest-sdk`:

```
podman run --rm -v /tmp/opencode/OpenPineBuds:/usr/src:Z -w /usr/src \
  --entrypoint /bin/bash ghcr.io/pine64/openpinebuds:latest-sdk \
  -c "make -j$(nproc) T=open_source DEBUG=1"
```

Resultado: `out/open_source/open_source.bin` (903.736 B) con LDAC enlazado.

### LDAC confirmado

- `config/open_source/target.mk`: `A2DP_LDAC_ON ?= 1`.
- `services/bt_app/a2dp_codecs/ldac/a2dp_codec_ldac.cpp`
- `apps/audioplayers/a2dp_decoder/a2dp_decoder_ldac.cpp`
- `thirdparty/audio_codec_lib/ldac/lib/ldac_lib.a` (Sony, 217 KB)
- Símbolos en el ELF: `a2dp_audio_ldac_init`, `a2dp_audio_ldac_decode_frame`,
  `a2dp_cp_ldac_cp_decode`, `a2dp_ldac_config`, ...
- AVDTP: vendor `0x012D`, codec `0x00AA`, 96/88.2/48/44.1 kHz.
- LHDC también está (`liblhdc-dec`, `services/lhdc_license`), con
  `A2DP_LHDC_ON ?= 0`.

### Diferencias con el firmware stock

| | Stock Fairbuds 1 | SDK PineBuds |
|---|---|---|
| CHIP | `best1501p` | `best2300p` |
| FLASH_BASE | `0x2C000000` | `0x3C000000` |
| FLASH_NC_BASE | `0x28000000` | `0x38000000` |
| FLASHX | `0x0C000000` | `0x0C000000` |
| OTA_CODE_OFFSET | `0x20000` | `0` |
| Boot info magic | `0x5A5A5A5A` | `0x5A5A5A5A` (mismo) |
| Build info trailer | texto | texto (mismo formato) |

El formato del trailer de build info coincide (`FLASH_BASE=...`,
`CRC32_OF_IMAGE=...`, `BUILD_DATE=...`). El `open_source.bin` del SDK sale
con `CRC32_OF_IMAGE=0x00000000` y `OTA_CODE_OFFSET=0`; hay que ajustar el
post-proceso/convert para generar una imagen OTA con offset 0x20000 y CRC.

### Trabajo del port LDAC

1. Cambiar el mapa de flash del SDK a `0x2C`/`0x28` y `OTA_CODE_OFFSET=0x20000`.
2. Adaptar `config/open_source/tgt_hardware.c` a la placa Fairbuds 1
   (GPIO, I2C, teclas, LEDs, ANC, batería, estuche).
3. Verificar diferencias `best1501p` vs `best2300p` (clocks, registros, BT).
4. Build con LDAC ON (ya funciona) y probar por OTA tras validar la stock.

### OTA por SPP

La app de Fairphone hace `startFirmwareUpgrade` con `PROTOCOL_SPP`
(RFCOMM), no BLE. El servicio SPP es `0x1101`; el canal RFCOMM sale del SDP.
Esto simplifica el cliente frente a BLE GATT.

## Parche SBC-XQ (imagen + APK)

- Imagen: `work/patched/fw_fairbuds_6_5_sbcxq.bin`
  - SBC caps `3f ff 02 35` (bitpool máx 53).
  - CRC32 del trailer: `0xDF6FC562`.
  - SHA-256: `eb99e824b0069357d588c729f0dde35abbadc07870484a67d5c15587f2fd9155`.
- APK parcheado: `work/patched/Fairbuds_6_6_sbcxq.apk`
  - `FirmwareBuild.FAIRBUDS` 6.5 -> 6.6 en smali.
  - `res/raw/fw_fairbuds_6_6.bin` = imagen SBC-XQ.
  - Firmado v1/v2/v3 con uber-apk-signer (debug key).
  - Requiere desinstalar la app oficial (firma distinta).
- Flujo: la app verá 6.5 en el dispositivo y 6.6 como última -> ofrece
  actualización -> usa su OTA SPP probado -> escribe la imagen SBC-XQ.

## VEREDICTO OTA (2026-11-19, pruebas 14:03-14:14)

> **REFUTADO** — ver "CORRECCIÓN: el `0x84 00` es el sanity CRC del trailer" al final.
> Los datos de la app llegaron exactos; el fallo era el contenido del trailer.

Secuencia real capturada con instrumentación `OTATX`/`OTADBG` en la app:

- Stock (CRC fichero completo `0xB7692D6E`):
  `0x88` -> `0x84 01` -> app envia `0x92` (commit) -> `0x93 01` => **apply/commit OK**.
- Solo-SBC (CRC `0x2D04E38B`, trailer stock intacto):
  `0x88` -> `0x84 00` => **falla antes del commit**.
- Parcheada (CRC `0x3F94DF2C`, trailer recalculado):
  `0x88` -> `0x84 00` => **falla antes del commit**.

Hechos:

- El `0x84` final es el **CRC del buffer recibido**, no una firma.
- Los `0x82` (CRC de segmento) solo reciben `0x83 01` (ack de datos);
  **nunca hay `0x84` por segmento**.
- La imagen stock commitea porque sus datos llegan correctos. Las imagenes
  modificadas fallan el CRC del buffer: los datos recibidos **no coinciden**
  con el fichero enviado.
- **No hay evidencia de firma/bloqueo**: el fallo es de **transporte/commit
  de datos** (RFCOMM del Samsung + flujo legacy `f11389b=0`), no de firma.
- El test stock **no es falso positivo**: hubo `0x92` + `0x93 01` (commit real).

Conclusiones:

1. La OTA acepta y commitea imagenes; el problema es la **integridad del
   transporte** en el movil Samsung.
2. Para flashear una imagen modificada hace falta un transporte fiable:
   - **PC con BlueZ SPP** al canal **13** (BES_OTA) y el protocolo de la app, o
   - movil con **delay entre chunks** mucho mayor (p.ej. 100-200 ms) y el
     flujo por segmentos.
3. Si el commit de una imagen modificada llega a `0x92` -> `0x93 01`, queda
   por probar el **secure boot** al arrancar. Ese es el siguiente gate real.

## Siguiente sesion

1. Cliente OTA en PC: SPP RFCOMM canal 13, protocolo `s9.d`/`s9.e`
   (handshake 0x8C, config 0x86, info 0x80, datos 0x85, CRC segmento 0x82,
   fin 0x88, commit 0x92). Generar random code y CRC como la app.
2. Probar con la imagen stock (debe commitear) y luego con la solo-SBC.
3. Si el commit funciona, reiniciar y comprobar si arranca (secure boot).

## CORRECCIÓN: el `0x84 00` es el sanity CRC del trailer (2026-11-19, 17:00)

### Algoritmo del check final (`0x88`), leído en dos fuentes que coinciden

- Stock 6.5: `0x2C0ACCE8`..`0x2C0ACF2C` (file `0x8CCE8`), pool `0x8CF48` -> `"CRC32_OF_IMAGE=0x"`.
- SDK OpenPineBuds `services/ibrt_ota/lib/lib_ibrt_ota_RTX.a`, con símbolos:
  `ota_bes_handle_received_data+0x7e2` (`ota_check_image_data_sanity_crc` inline).

Pasos:
1. Lee los últimos `0x200` B de la imagen recibida; busca `CRC32_OF_IMAGE=0x` (17 B).
2. Parsea 8 caracteres hex detrás (0-9a-fA-F).
3. `crc32(img[0 : kw_off + 17])` debe ser igual a ese valor. Los 8 dígitos NO entran
   en el rango (en la stock `kw_off = len-0x71`, rango = `img[:len-0x60]`).
4. Reescribe el primer word con `0xBE57EC1C` (en el fichero es `FFFFFFFF`).
5. `crc32(FFFFFFFF + img[4:])` debe ser igual al CRC del `0x80`
   (= CRC del fichero, porque el fichero empieza por `FFFFFFFF`).
Cualquier fallo -> `0x84 00`.

Opcodes reales (enum del SDK): `0x83` = `OTA_RSP_SEGMENT_VERIFY` (respuesta al `0x82`,
**no** es un ACK por chunk), `0x84` = `OTA_RSP_RESULT`, `0x8B` = `OTA_DATA_ACK`,
`0x8C/0x8D` = resume verify (breakpoint + random code), `0x90/0x91` = selección de lado,
`0x92/0x93` = image apply.

### Las pruebas de la app, explicadas

Datos reconstruidos de los logs `OTATX` (paquetes `0x85` concatenados):

| Sesión app | bytes == fichero | `0x82` -> `0x83 01` | final | causa |
|---|---|---|---|---|
| stock | sí | 51/51 | `84 01` -> `93 01` | correcto |
| solo-SBC (prueba C) | sí | 51/51 | `84 00` | trailer stock: sanity CRC no coincide |
| SBC-XQ (prueba B) | sí | 51/51 | `84 00` | trailer **roto**: CRC escrito en `kw+19` en vez de `kw+17` -> `CRC32_OF_IMAGE=0x1FDF6FC562UILD_DATE` -> parsea `0x1FDF6FC5` |

El transporte de la app es perfecto. No hay firma: la cabecera tiene `security=0x0000`
(`ff ff ff ff 00 00 04 00 ...`) y no hay bloque de firma.

Flujo real de la app: `0x97 01`, `0x8E`, `0x9D 01`, (10 s) `0x9B`, (10 s) `0x90 00` -> `0x91 01`,
`0x8C`, `0x80`, `0x86`, datos `0x85` de **660 B** cada ~34 ms **sin esperar nada**,
segmentos de 32768 B (último paquete del segmento recortado), `0x82` y **espera `0x83 01`**,
`0x88` -> `0x84`, `0x92` -> `0x93`. El cliente PC (252 B, sin `0x90`, sin esperar `0x83`
tras `0x82`) difiere; su fallo con la stock sigue sin explicar y ya no bloquea.

### Herramienta

`work/tools/besfw.py` emula el check del device (`check`), parchea y rehace el trailer
(`patch IN OUT OFF:OLD:NEW`) y tiene `selftest` con sabotajes: predice los tres resultados
reales de la app (stock `01`, C `00`, B `00`).

### Imagen SBC-XQ corregida

- `work/patched/fw_fairbuds_6_5_sbcxq_v2.bin`: `besfw.py patch ... 14423B:27:35`
  - trailer `CRC32_OF_IMAGE=0xDF6FC562`, CRC32 del fichero `0x4D6E0D2C`
  - SHA-256 `2ed30f2bd353e72cc0af33e66c2aa39338cf0a1151dede9a976b5be90776eba0`
  - diff con la stock: `0x14423B` + 7 dígitos del trailer.
- APK instrumentada: `work/arm64/signed/base_sbcxq2_unsigned-aligned-debugSigned.apk`
  (`res/raw/fw_fairbuds_6_6.bin` = v2, verificada con `besfw.py check`).
- La selección de config SBC (`0x2C0C0340`..) es intersección estándar
  (`max = min(remoto, local)`); no hay otro 39 fijo. El SDK BES usa 52 por defecto.
- Restaurar stock: `work/arm64/signed/base_stocktest_unsigned-aligned-debugSigned.apk`
  (ya commiteó la stock el 14:10).

## RESULTADO: SBC-XQ (bitpool 53) flasheado y funcionando (2026-11-19, 17:03-17:10)

- Flasheo con la app instrumentada en un Pixel 9 Pro XL (`base_sbcxq2`), log
  `work/logs/app_ota_sbcxq2_pixel.log`: datos enviados == `fw_fairbuds_6_5_sbcxq_v2.bin`
  (CRC `0x4D6E0D2C`), 51/51 segmentos `83 01`, `0x88` -> `84 01` -> `0x92` -> `93 01`.
- Arranca: tras el reinicio se reconectó al Pixel como dispositivo A2DP activo.
- Caps leídos por BlueZ tras re-emparejar el PC (`work/logs/pc_repair_probe.log`,
  D-Bus `.../sep1` Capabilities): SBC `3f ff 02 35` (max bitpool **53**), AAC sin cambios.
- Perfil PipeWire `a2dp-sink-sbc`: configuración negociada `11 15 02 35` (44.1 kHz,
  joint stereo, 16 bloques, 8 subbandas, bitpool 2..53). 30 s de streaming (barrido +
  ruido) sin desconexión ni errores.
- `avdtp_probe2.py` da `Connection refused` mientras BlueZ tiene A2DP conectado
  (canal de señalización AVDTP ya abierto); leer los caps por D-Bus en su lugar.
- Conclusión: el OTA acepta imágenes modificadas si el trailer es correcto; no hay
  secure boot que bloquee el arranque. Vía abierta para el port de LDAC.

## INCIDENTE: sin música tras las OTAs (2026-11-19, 17:05-17:40) — causa: EQ personalizado

Síntoma: tras las OTAs, sin audio A2DP en ambos cascos con cualquier codec y cualquier móvil
(Pixel y otro), aunque el reproductor avanzaba. Avisos de voz ("Connected", modos ANC) y
ANC/transparencia funcionaban.

Descartado: el parche SBC-XQ. Se restauró la stock 6.5 exacta (`84 01` -> `93 01`,
log `work/logs/app_ota_restore_stock_pixel.log`) y seguía sin sonar.

Causa: EQ. Al elegir un preset predefinido en la app volvió el sonido.
- `set_user_eq_mode` (`0x2C11F8E0`): modos 0-4 = presets; **modo 5 = EQ personalizado**, carga
  8 bandas x 3 B de la sección reservada `0x283FE000` (copia en RAM `0x200AB6C0`, leída al
  arrancar en `0x2C11FA38`; byte 0 = `eqmode`, byte `0x19` = `Wear_det_disable`).
  Bandas inválidas/borradas -> música en silencio; los avisos y el ANC no pasan por ese EQ.
- `eqmode` también está en `nvrecord_ble_data` (datos de usuario, `0x283EC000`).
- La app manda en **cada** OTA el config `0x86` con flags `0x1f` (fijo en `s9/d.java`):
  `isToClearUserData` (`nv_record_rebuild`) + renombrar BT/BLE + reescribir direcciones BT/BLE
  en la sección factory (la oficial hace lo mismo). Es la sospechosa de dejar el EQ en un
  estado inválido; no está demostrado.

Regla: tras cualquier OTA, si no hay música, re-seleccionar un preset de EQ en la app.

Otros datos de esta investigación:
- `0x89` (leer flash) responde `89 01` pero ni la stock ni el SDK envían los paquetes `0x8A`:
  no sirve para volcar flash.
- Avisos de voz y ANC no pasan por el EQ de música.

## Port LDAC

Ver `work/LDAC_PORT.md` (fase 1 de viabilidad completada el 2026-11-19).
Estado de los cascos al cierre: **SBC-XQ v2 flasheada** (`4D6E0D2C`), funcionando.

## Cliente OTA de PC validado (2026-11-19, ~18:40) — vía de recuperación sin móvil

`work/tools/pc_ota3.py` (copia el flujo de la app: `0x97`, `0x8E`, `0x90 00`, `0x8C`, `0x80`, `0x86`,
paquetes de MTU-9 = 660 B, segmentos de 32768 B esperando `0x83 01` tras cada `0x82`, `0x88`, `0x92`
solo tras `84 01`). Config `0x86` con **flags 0** (sin borrar datos de usuario ni reescribir factory);
con `--app-flags` genera byte a byte el config de la app.
Prueba: reflasheo de la SBC-XQ v2 instalada desde el PC (Pixel con BT apagado): 51/51 segmentos,
`84 01` -> `93 01` en **57 s**; los cascos se reinician, reconectan y anuncian `3f ff 02 35`.
Log: `work/logs/pc_ota3_20261119_190344.log`. El cliente se niega a enviar imágenes que fallarían el check del device (besfw).

## Etapa 3a superada: imagen más larga con bloque añadido (2026-11-19, ~19:25)

`work/patched/ldac/stage3a_blob_nohooks.bin` (SBC-XQ + blob de 636 B inerte en `0x0C1B7090` + copia del
trailer al final; 1.668.280 B; CRC `0x4F8417C2`), montada con `work/tools/build_ldac_image.py --no-hooks`.
Flasheo por PC (`pc_ota3.py`): `84 01` -> `93 01` en 62 s. Tras el reinicio: reconecta, anuncia SBC
`3f ff 02 35` y AAC, y el OTA responde a `0x8E`. Conclusión: el bootloader acepta y arranca imágenes
más largas que la stock con el trailer reubicado.

**Ojo: con los cascos dentro del estuche el canal OTA (RFCOMM 13, `BESOTA`) acepta la conexión pero
no responde a nada.** Sacarlos del estuche antes de cualquier OTA. (El SPP de OTA no aparece al
buscar `0x1101` en SDP: es el registro `BESOTA` con UUID `66666666...`, canal 13; `TOTA` es el 12.)

## LDAC en los cascos: primeras pruebas (2026-11-19, noche) y estado al cierre

- v1: arranca y anuncia LDAC (SEP 3 `2D 01 00 00 AA 00 30 01`), OTA vivo; silencio en LDAC.
- Causa: **el evento `0xd` es `A2DP_EVENT_STREAM_RECONFIG_IND`**; la apertura normal (eventos `2`/`0x1f`)
  trata cualquier códec no AAC como SBC (`+0x4a=0`, `+0x3f=elements[0]&0xF0`). Nuevo gancho **H5** en
  `0x0C0BFCA8` (`ldrb r3,[r5]; cmp r3,#2` -> `bl ldac_open_hook`). Con H5 el decoder LDAC entra en uso.
- `ldac_lib` necesita **16.272 B** al iniciarse (medido); el syspool del stock no los tiene durante un
  stream -> ahora syspool si cabe, si no el heap de audio (7 reservas, tabla de 8 en el hueco de RAM).
- v4d (CPU `app_sysfreq_req(0xf, 8)`): **el barrido se oye por LDAC**, pero con **chasquidos muy fuertes**
  entre medias (muestras corruptas a escala completa). Incidente: el usuario estaba escuchando a
  volumen normal. Se restauró la SBC-XQ v2 por PC (`84 01` -> `93 01`); verificado: solo SBC (bitpool
  53) y AAC, sin SEP LDAC.

**Regla a partir de aquí: ninguna prueba de audio experimental con los cascos puestos.** Primero
capturar el stream LDAC real (btmon / PipeWire) y reproducir el decodificado en el emulador hasta que
salga limpio; luego validar en los cascos fuera de las orejas, con el volumen del PC al mínimo.

## v5a en los cascos (2026-11-19, ~21:30): sigue sucio, con cortes y chasquidos -> restaurada v2

- v5a (heap, sin boost de CPU, búfer de arrastre, sin tonos, fundidos) pasa todas las pruebas offline
  (108,6 dB, continuidad perfecta con peticiones irregulares, sabotajes detectados), pero en los cascos
  (sobre la mesa, PC al 15 %, barrido a -21 dBFS) suena **sucio, con cortes y chasquidos**.
- Conclusión: el decoder es correcto en aislamiento; el problema está en la integración en tiempo
  real (CPU del MCU ejecutando LDAC desde flash/XIP, heap/descartes, sincronización TWS del framework),
  que la emulación no reproduce. **Sin observabilidad no se puede seguir probando a ciegas.**
- Restauración: un primer intento falló un segmento (`83 00`, abortado sin aplicar); el segundo
  (`--pace-ms 12`) OK. Tras reconectar, BlueZ muestra solo SBC (bitpool 53) y AAC. (La primera
  comprobación aún listaba el SEP LDAC: era el estado de antes del reinicio; hay que forzar
  desconexión/reconexión antes de verificar.)

Siguiente paso propuesto (sin pruebas de escucha): canal de estadísticas leíble desde el PC (sin
tocar el camino OTA) + medida de ciclos con DWT CYCCNT por llamada a decode, nº de underflows,
frames descartados por memoria, resincronizaciones. Con eso decidir: CP (segundo núcleo), código en
RAM, o más frecuencia.

## v6a: estadísticas LDAC legibles por el SPP de OTA (2026-11-19, sin pruebas de escucha)

- **H6** `0x0C0AD072` (`subs r3,#0x80; cmp r3,#0x19` del despachador OTA `FUN_0c0ad038`, justo antes de
  `bhi` → salida "opcode desconocido" `0x0C0AD132` y la `tbh` de opcodes 0x80..0x99) → `bl ldac_dbg_hook`.
  Solo `A5 'BEST'` hace algo: responde por la ruta "legacy" de `ota_control_image_apply_rsp`
  (`0x0C0AC2C8`: rol `[0x2008A060]==2`, env `0x2008A844`, `env+0xB0 != 1`, transmit `*(env+0x98)(buf,len)`)
  con `A6 54 <ldac_stats_t>` (o `A6 00 swap cfg` sin stream) y sale por la rama de opcode desconocido.
  El resto de opcodes ejecuta exactamente las dos instrucciones originales (probado 0x80..0xFF).
- Estadísticas por stream (bloque del heap A2DP junto a lastframe, lista de reservas de ldac_lib y PCM;
  el hueco de RAM no tiene sitio): llamadas/frames/frames malos/underflows, muestras pedidas, tiempo
  dentro de decode con el **timer de sistema** `0x40002004` (accesor stock `0x0C0276E8` = `-valor`;
  DWT/DEMCR evitados: la stock solo usa DWT para calibrar y lo deja apagado, y el SCS exige privilegio),
  descartes por memoria/limitador, máximos de lista, sync/discards, heap libre mínimo.
  Lector PC: `work/tools/ldac_stats.py` (calcula carga = busy/wall y calibra el timer con muestras/fs).
- **Unicorn 2.1.4**: un `UC_HOOK_MEM_WRITE` global deja un estado IT obsoleto tras un store
  condicional (`it lo; strlo.w`) y el bloque siguiente se salta una instrucción; redirigir PC desde un
  hook de código tampoco es fiable. `emu_decoder.py` ahora usa stubs `bx lr` reales y un diff de RAM
  para escrituras fuera de sitio (sabotaje "stray write" detectado).
- Verificación: emu_decoder ALL PASS (108,6 dB, continuidad, estadísticas exactas), emu_hooks ALL PASS
  (H1..H6), sabotajes: 5/5 decoder, 8/8 ganchos.
- Imagen: `work/patched/ldac/ldac_v6a.bin` (CRC 0xCE79BC11). Plan en el device: leer stats con SBC
  (espera "sin stream"), luego LDAC con volumen 0 % y silencio digital; nadie escucha.

## Medidas en el device con estadísticas (v6a..v6h, 2026-11-19 22:20-23:05; volumen 0 %, silencio, nadie escucha)

- Decoder correcto en el device: 0 frames malos, 0 underflows, 0 descartes por memoria; entrada exacta
  375 frames/s (48 kHz, 3-4 frames/paquete, ~200 B/frame).
- Reinicios del decoder cada 3-9 s (`inits` sube). Primero se atribuyó al limitador (lista ~180-197
  con límite 200 frames: `store` -4 → `FUN_0c127274` reintenta, `discards_samples` y resync TWS
  `FUN_0c08c604`+`FUN_0c07e5a8`); con el límite a 400 (v6b) el limitador ya no salta pero los
  reinicios siguen.
- Causa real: el consumo es ~3 % menor que la entrada (363 vs 375 frames/s) porque ~2 llamadas/s a
  `decode` duran más que el periodo DMA (768 muestras = 16 ms; `slow` ~2/s, huecos de hasta ~34 ms):
  el DMA pierde ~2 de 62,5 periodos/s (= 3,2 %) → chasquidos/cortes, la lista crece y el framework
  reinicia. El factor de velocidad del framework (`ctx+0xc`, `stream_info.factor_reference`) se queda en
  0,99992..1,00292: no es el sintonizador. (`ctx+0x16` vale 0: no es `dest_packet_mut`.)
- Carga dentro de `decode` ~46 % del tiempo real, de ella 43 % en `ldacBT_decode`. Con
  `app_sysfreq_req(0xf, 8)` la CPU pasa de ~104 a ~208 MHz (bucle de calibración 197 → 95 ticks del
  timer de 16 kHz) y la carga NO baja: `ldac_lib` está limitada por la ejecución desde flash (XIP),
  no por la CPU. La stock ejecuta sus decoders desde RAM (overlays).
- Siguiente paso: ejecutar el blob (25,5 KB, código+tablas) desde una copia en el heap A2DP vía alias
  RAMX, reubicado en tiempo de ejecución (ABS32, MOVW/MOVT; llamadas externas solo por thunks
  absolutos), verificado en Unicorn con RAM/RAMX aliasadas; medir de nuevo con estadísticas.
- Procedimiento: tras reconectar, la primera configuración LDAC puede no llegar al casco (`cfg_flag 0`):
  alternar el perfil SBC-XQ → LDAC dos veces. Tras restaurar, verificar endpoints tras una segunda
  reconexión (la primera muestra la caché).
- Restaurada SBC-XQ v2 a las 23:08 (verificado: solo SBC bitpool 53 y AAC).

## v7: LDAC estable en el device (2026-11-20, medido sin escuchar: volumen hw 0, nadie lleva los cascos)

- **Causa raíz real de chasquidos/cortes**: la CPU iba a ~104 MHz durante la reproducción. La prueba de
  "boost" de la v6g era inválida: `app_sysfreq_req(0xf, 8)` en `ldac_init` sí subía a ~208 MHz (bucle
  de calibración 197 → 95 ticks en init), pero la stock reescribe el usuario A2DP 0xf después del init
  (medido en `store` durante la reproducción: 47 ticks/100 000 iteraciones = 104 MHz).
- Arreglo: usuario propio de sysfreq **0x1e** (la stock usa 0x0, 0x5, 0xc..0x1b; `hal_sysfreq_req`
  acepta 0..0x25, gana el máximo) nivel 8 en init, liberado (`(0x1e, 0)`) en deinit.
- Además (v7a): `ldac_lib` se ejecuta desde una copia reubicada en el heap A2DP (alias RAMX), payload
  de 22 KB enlazado en 0x10000000 (`work/tools/mkpayload.py`, solo ABS32, `-mno-movt`, checksum,
  alineación a 8 hecha por el cargador). Por sí sola NO mejoró la carga (49-51 % a 104 MHz: no era la
  flash); se mantiene porque funciona en el chip (bloque 0x20020020) y libera el hueco de RAM.
- Resultado v7d (48 kHz, silencio y ruido rosa -6 dBFS con volumen hw 0): CPU 208 MHz en reproducción
  (`cpu_run` 23-24), carga en decode 21-22 %, llamada máx 3,6 ms (periodo 16 ms), 0 huecos tardíos,
  0 llamadas lentas, 0 reinicios en 30 s, 0 frames malos, 0 underflows, entrada = salida (~48 k
  muestras/s), heap libre mín. 238 KB.
- Sin decodificar (carga del payload fallida por alineación, v7b) el stream ya era estable: confirma
  que todo el problema era el tiempo de decode.
- Pendiente: prueba de escucha cuidadosa (cascos en la mesa, volumen bajo), 44,1 kHz, el casco
  esclavo TWS (solo se leen estadísticas del maestro; sus reinicios se verían como `inits`).
- **Escucha 2026-11-20 ~09:57** (v7d, cascos sobre la mesa, PC 15 % = volumen AVRCP 19/127, barrido
  -21 dBFS 20 s, dos veces, con vigilante que corta a la primera anomalía: `guarded_play.py`):
  el usuario oye "barrido continuo y limpio, sin cortes ni chasquidos"; estadísticas limpias en ambas
  (0 bad/underflow/late/slow/reinicios, CPU 208 MHz, llamada máx 3,8-5,2 ms).

## v7e/v7f: sincronía TWS y objetivo de caché (2026-11-20 ~10:15-10:45)

- "Estéreo desfasado" (retardo ligero entre oídos, opción a): `discards_packet(n)` descartaba n frames;
  la stock SBC (overlay 1, 0x00201144) alinea al inicio de paquete (`cur == 0`) y descarta n
  PAQUETES enteros (-3 si no hay). El esclavo TWS lo usa para alinearse con el maestro → quedaba
  2-3 frames (5-8 ms) por detrás. Arreglado en v7e; el usuario ya no percibe retardo.
  (Comprobado también: `sync_packet` (máscaras 0x1/0xf), `headframe_info`, `sync_dest_packet_mut`,
  `convert_list_to_samples`, `discards_samples` y `store` (seq/ts de paquete, cur desde 0, total)
  coinciden con la stock; `channel_select` de la stock tampoco hace nada.)
- Cortes al cabo de ~2 min (underflows): el sintonizador de velocidad del framework compara la media
  de entradas en lista (`ctx+0x1c`, `(3·media+mut)/4`, 0x0C128C96) con `ctx+0x16` (dest_packet_mut,
  traza "tune ratio %d mut:%d->%d"). El llamador de `a2dp_audio_init` (0x0C113540) pasa SBC 35,
  AAC 3 y para cualquier otro códec 0.0+0.0 = 0 → factor clavado en 1.00292 → la lista se vacía
  (~1 frame/s) → underflows. v7f: `ldac_init` pone 75 frames (200 ms a 48 kHz) si viene 0
  (a2dp_audio_init lo escribe justo antes de llamar a init). Resultado (canción 214 s, volumen 0):
  lista estable en ~66-70, factor 0,9997-1,0006, 0 underflows/late/slow/reinicios.
- Test del emulador con canales distintos (L 1 kHz, R dos tonos inconmensurables): L/R correctos y
  alineados (>100 dB). Sabotajes nuevos: L/R intercambiados, discards_packet por frames,
  dest_packet_mut a 0.
- OTA desde PC: a veces un segmento devuelve `83 00` y se aborta sin aplicar (seguro); reintentar.

## v7g y estado a las ~11:35 (2026-11-20)

- Prueba audible de la canción entera (v7f, 20 %, mesa) con un ratón Bluetooth conectado al mismo
  adaptador del PC: cortes, resincronizaciones ("solo izquierda") y aceleraciones. Causa en el PC:
  `spa.bluez5.sink.media: Missing completion reports for packet ... adapter firmware bug?` a las
  11:11:07. Sin el ratón, la canción entera: 0 underflows/late/slow/reinicios, lista ~62.
- El sintonizador de la stock acelera +0,29 % pero apenas frena (0,99992): el nivel perdido en un
  hueco de radio no se recupera hasta un underflow+reinicio. v7g: objetivo 120 frames (320 ms).
  Tests y 14+8 sabotajes OK; `work/patched/ldac/ldac_v7g.bin`, NO flasheada aún.
- Con batería 20 %: la sonda OTA `0x8E` no respondía (en TWS el maestro consulta al otro casco antes de
  contestar) y un OTA completo se quedó sin acuse del último segmento con reinicio del casco (inits a 2):
  probable caída de tensión al escribir flash. Nada aplicado; siguen con v7f. Cargar antes de flashear.
- El vigilante de escucha (`guarded_play.py`) ahora corta el sonido ante cualquier error propio
  (antes, un timeout de estadísticas dejó la canción sonando sin vigilancia).

## Ecualización con LDAC (2026-11-20)

- Usuario (S25 Ultra, LDAC elegido a mano): "suenan muy bien, la ecualización ha cambiado un poco".
- El EQ del casco no depende del códec: la única llamada a la función que lo aplica
  (`FUN_0c113180(type, index)`, traza `[EQ] set type=%d,index=%d`, IIR por software) está en
  0x0C113228 y pasa `(0, FUN_0c11f4a0())` = el preset actual de la app. En el arranque del stream
  (0x0C113540) lo que cambia por códec es: `ctx+0x118` (SBC 0xAF, AAC 0x18), `ctx+0x11A`,
  `dest_packet_mut` (SBC 35, AAC 3, otros 0) y `sysfreq (0xf, 4)` en la rama "sbc 48k freq";
  nada de procesado de sonido.
- La diferencia percibida viene de la fuente: ancho de banda de LDAC (660/990 kbps hasta ~20-24 kHz;
  330 kbps recorta), el AAC del móvil (recorte ~16-18 kHz, 44,1 kHz) y el procesado del móvil por
  códec. Sin micrófono no se puede medir aquí.
- v7g flasheada con el procedimiento publicado (`build_all.sh` + `pc_ota3.py` con config de
  direcciones a cero y dirección autodetectada): COMMIT OK, nombre y dirección del casco intactos,
  `dest_mut` 120, lista ~112, 0 errores.
