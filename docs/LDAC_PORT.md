# Port de LDAC a Fairbuds 1 (stock 6.5 + parche aditivo)

Fecha: 2026-11-19. Estado: **fase 1 (viabilidad) completada; sin bloqueos duros**.

## Enfoque

Parche **aditivo** sobre la stock 6.5 (no firmware completo del SDK):

- El arranque y el OTA quedan idénticos a la stock: siempre se puede volver a la stock por OTA.
- El único cambio que se ejecuta al arrancar es **registrar un SEP más** (ver abajo); si falla,
  devuelve 0 (nadie lo comprueba) y LDAC simplemente no se anuncia.
- Todo lo demás solo se ejecuta si el móvil negocia LDAC. Si eso fallara, el casco se reinicia al
  reproducir en LDAC, pero el OTA (SPP) sigue funcionando: forzar SBC/AAC en las opciones de
  desarrollador del móvil y reflashear.

Firmware completo del SDK OpenPineBuds descartado: sustituye arranque, TWS, estuche, sensores y OTA.

## Hechos medidos (fase 1)

| Recurso | Dato | Fuente |
|---|---|---|
| Tamaño máx. de imagen OTA | `0x1E0000` (1.966.080 B) | `0x2C0AB148` (`mov.w r0,#0x1e0000`), usado por el check "ImageSize ... greater than" |
| Imagen stock | `0x197084` (1.667.204 B) | — |
| **Flash libre** | **~292 KB** | diferencia |
| Mapa de flash | app `0x20000`–`0x200000`, staging `0x200000`–`0x3E0000`, aud `0x3EA000`, user `0x3EC000`, ANC `0x3EE000`, reservada (EQ) `0x3FE000`, factory `0x3FF000` | trailer + código |
| `ldac_lib.a` (SDK) | **21.550 B text**, 96 B data, 32 B bss | `llvm-size` |
| Glue SDK | `a2dp_decoder_ldac.cpp` 1058 líneas, `a2dp_codec_ldac.cpp` 99 | — |
| Framework de decoders en la stock | presente: `a2dp_audio_init`, `a2dp_audio_heap_{malloc,cmalloc,free,realloc}`, `a2dp_cp_*`, `cp_accel_open/close`, `a2dp_cp_{sbc,aac_lc}_cp_decode` | strings + xrefs |
| Vendor codecs en AVDTP | camino presente (`"(d%x) codec type=%d/LHDC or LDAC"`, stub LHDC) | `0x2C1709B4` |
| Ejecución | el código corre desde el alias `0x0C0xxxxx` (= flash `0x2C0xxxxx`) | callbacks `0x0C0BFBC9` |
| RAM | MSP `0x20180000`, MSPLIM `0x2017DF00`, `.data` `0x20080840`–`0x20083014` (LMA `0x2C1B4704`) | reset handler `0x14` |

### Registro de SEPs (stock)

- `0x2C0BF8B0`..: `ctx = 0x2C0BCA00(dev)`; `ctx+0x34 = btif_a2dp_alloc_stream()` (`0x2C103524`):
  **un solo stream por dispositivo**.
- `0x2C0C2894` (SBC) y `0x2C0C2858` (AAC) llaman a
  `btif_a2dp_register(stream=ctx+0x34, role=1, desc, 0, sep_id, cb=0x0C0BFBC9)` = `0x2C103444`
  (lock `0x2C0E5E1C` → `0x2C0EF0CC` → unlock `0x2C0E5E54`; devuelve bool).
  - descriptor SBC `0x2008F5C8`: `+0` u16 `0x0400` (codec 0x00, len 4), `+2` caps `0x2C164238`,
    `+0xA` u8 1; sep_id 2.
  - descriptor AAC `0x2008F5BC`: `0x0602` (codec 0x02, len 6), caps `0x2C164230`; sep_id 3.
- **LDAC**: tercera llamada con descriptor `0x08FF` (codec 0xFF vendor, len 8), caps
  `2D 01 00 00 AA 00 <fs> <cm>`, sep_id 4. Necesita 12 B de RAM persistente para el descriptor.

### Ramas `A2DP_LDAC_ON` del SDK que hay que reproducir

| Fichero SDK | Qué hace | Equivalente stock (pendiente) |
|---|---|---|
| `app_a2dp.cpp` | detectar vendor `2D 01 00 00 AA 00` en el config, `codec_type = NON_A2DP`, `sample_bit = 16`, `a2dp_ldac_config()` (fs/cm), quitar cabecera +1 B en `store_sbc_buffer` | callback `0x0C0BFBC9` y stub LHDC (`0x2C0C0734`, llamado desde `0x2C0C0822`) |
| `a2dp_codec_ldac.cpp` | caps LDAC | nuevo |
| `a2dp_decoder.cpp` | `case A2DP_AUDIO_CODEC_TYPE_LDAC` → `a2dp_audio_ldac_decoder_config` (default = ASSERT) | `a2dp_audio_init` (string `0x186EF8`) |
| `a2dp_decoder_ldac.cpp` | decoder: tabla `A2DP_AUDIO_DECODER_T` (stream_info + 14 punteros) | nuevo, enlazado contra funciones stock |
| `app_bt_stream.cpp` | fs 88,2/96k, overlay `APP_OVERLAY_A2DP_LDAC`, retardo, `APP_SYSFREQ_52M/104M`, buffers DMA | por localizar |
| `pool_api.c` / `heap_api.h` | tamaños de pool | por medir |
| `app_bt.cpp` | rol/reconexión del stream LDAC | con un solo stream, probablemente no aplica |

## Decisiones para la primera versión

- Anunciar solo **44,1/48 kHz, salida 16 bits** (evita tocar 88,2/96k y 24 bits en `app_bt_stream`).
- Decodificar en el **MCU** (`a2dp_audio_ldac_mcu_decode_frame`), sin CP: el SDK sube el MCU a
  52 MHz para LDAC a 48k. El CP se deja para después.

## Fase 2 — resultados

- **Arranque seguro (hecho):** `a2dp_register_sep` (`0x2C0EF0CC`) recorre los huecos de SEP del
  stream (`stream+0x68`, lista enlazada por `+0x14`); sin hueco -> traza
  `"a2dp_register_sep no sep resource"` y devuelve error, sin assert. La init de streams
  (`0x2C1034A6`) crea **3 huecos de 0x18 B por stream** (2 streams). SBC+AAC usan 2: **queda 1**.
- **RAM estática (hecho):** `.bss` = `0x20083014`–`0x200BC734` (memset en `0x2C06E50E`);
  heap newlib = `0x200BC800`–`0x200BD800` (**4 KB**; puntero `0x20080A84`; `_sbrk` `0x2C087A28`
  hace **assert** al desbordar -> `malloc` en el arranque NO es seguro).
  **Hueco libre: `0x200BC734`–`0x200BC800` (204 B)**, sin ninguna referencia en el firmware y sin
  inicializar al arrancar (el código nuevo debe inicializarlo). Uso previsto: descriptor SEP 12 B +
  `.data` 96 B + `.bss` 32 B de `ldac_lib`; el estado grande del decoder, al heap de audio.


### Mapa del firmware para el port (fase 2)

- **El código se ejecuta en `0x0C020000`** (alias FLASHX); los datos/cadenas se leen en `0x2C020000`.
  Punteros a función y saltos absolutos usan `0x0C......`.
- Copias a RAM en el arranque (`0x2C0214D4`/`0x2C021440`): `0x1788..0x90F4 -> 0x20060180`,
  `0x90F4..0x19A74 -> 0x2006FE40`, `0x19A74..0x21F34 -> 0x20180000`; `.data` `0x194704 -> 0x20080840`.
- **Overlays** (cargador `0x200788A4`, máx. 12 IDs). Texto: tablas `0x2007B9B0`/`0x2007B9E0`, se ejecuta
  en `0x00200CC0` (RAMX de `0x20000CC0`). Datos: tablas `0x2007B950`/`0x2007B980`, en `0x2001C29C`.
  ID1 = decoder SBC (texto `0x2C0517A8`, datos `0x2C066388`), ID4 = AAC (`0x2C0525F8`, `0x2C066BE4`).
  El reproductor elige overlay con `FUN_0c10da88(1|4)`. **LDAC no usará overlay**: corre desde flash.
- Helpers libgcc en RAM: `__aeabi_f2d` `0x20072315`, `__aeabi_d2iz` `0x200728F9` (bytes idénticos al
  libgcc del SDK). `floor`/`__aeabi_d2lz`/`memcpy`/`memset` propios (`ldac/src/rt_helpers.c`,
  test de host con 4 M de casos + sabotajes en `ldac/test/`).

### Camino de un stream A2DP en la stock

1. Callback A2DP `0x0C0BFBC8` (`cb` de `btif_a2dp_register`):
   - evento `3` (el casco inicia la config): construye config para SBC (`codec 0`) y AAC (`2`); otros -> nada.
   - **evento `0xd` (config del stream)**: guarda en el contexto del dispositivo (`0x0C0BCA00(dev)`):
     `+0x4a` tipo de códec (0 SBC / 2 AAC), `+0x40` bits (`0x10`), `+0x3f` frecuencia como flag SBC
     (`0x20` 44,1k / `0x10` 48k), `+0x42` modo de canal (1/2). Otro códec -> `break` (sin assert).
   - evento `0x12` (paquete): quita la cabecera RTP (`0x0C10354C`) y llama a `0x0C127274` (store).
2. Reproductor `FUN_0c1132f8` (start): pide el códec; `0` -> `codec_type 1` (SBC), `2` -> `2` (AAC),
   **otro -> 1 (SBC)**. Buffer DMA `0x2800` (44,1k) / `0x3000` (48k) / `0x4000` (AAC); salida 24 bits.
3. `a2dp_audio_init` = `FUN_0c12759c(…, codec_type, cfg, chnl_sel, …)`: `1` -> `memcpy(ctx+0x20,
   sbc_cfg 0x2001CA80, 0x70)`, `2` -> AAC `0x2001CC70`; **otro -> ASSERT (reinicio)**. Contexto
   `0x200B0318`; llama a `init` en `ctx+0x48`.

### Tabla del decoder en la stock (`0x70` B)

`+0x00` sample_rate · `+0x04` u8 canales, u8 bits · `+0x08` frame_samples · `+0x0C` factor (float) ·
`+0x10` auto_sync · `+0x14` 5 palabras nuevas (0) · **`+0x28` 14 punteros en el orden del SDK**
(init, deinit, decode_frame, preparse, store, discards_packet, sync_packet, sync_dest_mut,
convert_list_to_samples, discards_samples, headframe_info_get, info_get, packet_free,
channel_select) · `+0x60` 4 palabras (0).

### Diseño de ganchos (cerrado): 4 parches de 4 B

Marca de LDAC = **`dev+0x4a = 0xFF`** (el valor del SDK para códecs de fabricante). Motivos:
- La sincronización TWS (maestro `0x0C0BF6F8` lee `+0x4a,+0x40,+0x3f`; esclavo `0x0C0BF714` los
  escribe) lo transmite sola al esclavo. El "vendor para" del log no se usa.
- El reproductor `FUN_0c1132f8` trata cualquier códec != 0/2 como SBC (`codec_type 1`), es decir, el
  camino de H3. Las 15 lecturas de `+0x4a` del firmware lo tratan de forma genérica; el stub LHDC
  (`FUN_0c0c07a8` -> `0x0C0C0734`) confirma que el diseño original guardaba `0xFF`.
- Solo se anuncia **estéreo, 44,1/48k**: el esclavo puede iniciar el decoder con lo sincronizado.

| Gancho | Dirección | Original | Nuevo | Contrato |
|---|---|---|---|---|
| H1 arranque | `0x0C0BF8FA` | `bl 0x0C0C2858` (registro AAC, r0=dev) | `bl ldac_boot_hook` | llama al original; init RAM del hueco; `btif_a2dp_register(ctx(dev)+0x34 stream, 1, &ldac_desc, 0, 4, 0x0C0BFBC9)` (`0x0C103444`) |
| H2 config `0xd` | `0x0C0C027A` | `cmp.w r8,#0` (tras él: `bne 0x0C0C0318`) | `bl ldac_cfg_hook` | asm; conserva todo salvo flags; r3=codec struct, r4=dev ctx, r8=tipo. SBC -> Z=1. Sony LDAC -> `+0x4a=0xFF,+0x40=0x10,+0x3f=fs(0x20/0x10),+0x42=2`, Z=0. Otro -> Z=0 |
| H3 tabla decoder | `0x0C127876` | `bl memcpy 0x0C13B198` (dst `0x200B0338`, src SBC `0x2001CA80`, 0x70) | `bl ldac_dec_copy_hook` | si `ctx(cur_dev 0x0C0C3D44)+0x4a==0xFF` -> src = tabla LDAC; luego memcpy |
| H4 config iniciada por el casco (evento 3) | `0x0C0C00AC` | `cmp r3,#2; bne.w 0x0C0C0318` (6 B) | `bl ldac_cfginit_hook; nop` | asm; vuelve a `0x0C0C00B2` (AAC) o `0x0C0C0318`; si LDAC escribe 8 B de config en el buffer (`r8+2` -> `0x0C0C017C`, 10 B) |

Funciones stock usadas: `0x0C0C3D44` dispositivo A2DP actual; `0x0C0BCA00(dev)` contexto
(`dev*0x130+base`, NULL si dev>=2); `0x0C103444` `btif_a2dp_register`; `0x0C0C2858` setup AAC;
`0x0C13B198` memcpy.

### Ganchos implementados y verificados en emulación (2026-11-19)

- Código: `ldac/src/hooks.c` (H1, H3 y la lógica de H2/H4), `hooks_asm.S` (trampolines H2/H4
  con registros exactos), `stock.h` + `stock_syms.ld` (direcciones con evidencia).
- LR: H2/H4 sustituyen instrucciones que no eran `bl`; el único uso de LR como temporal en el
  callback (`0x0C0C00FA` escribe, `0x0C0C00FE`/`0x0C0C0708` leen) está dominado por esa escritura y
  en otro `case` del switch: los ganchos no rompen nada vivo.
- `tools/build_ldac_image.py`: monta la imagen (stock + SBC-XQ + parches + blob + copia del
  trailer), verifica bytes originales, autocomprueba el codificador de `bl` con los BL originales de
  H1/H3, permite solo las diferencias previstas, límite `0x1E0000`, check del device.
- `ldac/test/emu_hooks.py` (Unicorn, venv `.venv`): ejecuta cada gancho desde su sitio real
  en la imagen parcheada con el firmware cargado; comprueba punto de retorno, r0-r12/sp, flags,
  todas las escrituras de memoria (solo zonas permitidas) y argumentos a la stock (stubs):
  H1 dev0/dev1/dev2/stream NULL, H2 (LDAC 48k/44.1k, 96k no anunciado, aptX, SBC, otro), H4 (AAC,
  LDAC con intersecciones, 96k-only, mono-only, aptX, otro), H3 (SBC, LDAC con tabla no lista).
  `sabotage_hooks.py`: 4 sabotajes (flags H2, retorno H4, caps, destino BL H3) -> todos detectados.

### Decoder LDAC v1 (2026-11-19)

`ldac/src/ldac_decoder.c`: camino MCU del glue LDAC del SDK adaptado a las estructuras stock
(frame 0x14, lastframe 0x34 con 4 B extra al inicio, contexto `+0x16`/`+0x90`, cabecera RTP seq `+4`
ts `+8`). Sin asserts: comprueba `a2dp_audio_heap_info` y `syspool_free_size` antes de reservar;
decodifica en un buffer temporal de 1024 B (nunca escribe fuera del buffer del framework); aprende el
tamaño real de frame. Diagnóstico audible: tono grave = no se pudo crear el decoder; tono agudo = un
frame no decodificó. Lista liberada por el callback del framework (`0x0C126798` -> `ctx+0x78` =
`packet_free` de la tabla copiada). Prioridad del SEP = 1 (SBC 2, AAC 3; dentro de cualquier rango
que ya use el stock).

Verificación (`ldac/test/emu_decoder.py`): seno 1 kHz codificado en el host con AOSP libldac
(`libldacBT_enc.so`, HQ 990 kbps, MTU 679 -> paquetes de 660 B con 2 frames), decodificado por el
código ARM real del blob en Unicorn (Cortex-M33 + FPU) con el framework simulado: **SNR 108,6 dB a
48 y 44,1 kHz** (retardo 128 muestras), underflow -> -5 y ceros, limitador -> -4, sin escrituras
fuera de la memoria permitida, sin dobles liberaciones. Sabotajes (longitud de frame, underflow)
detectados.

Imagen: `patched/ldac/ldac_v1.bin` (1.691.852 B, CRC `0x3C42F75A`, SHA-256 `bf4a2e00…`).

### Estado al cierre del 2026-11-19

- Cascos: **SBC-XQ v2** (sin LDAC). Imágenes LDAC: `patched/ldac/ldac_v{1,1d,2d,3d,4d}.bin`.
- Funciona en hardware: arranque con H1, SEP LDAC, H5 (apertura), H3 (tabla), decoder en uso, `ldac_lib`
  creado, audio LDAC audible.
- **Bloqueante: chasquidos fuertes** (muestras corruptas). Hipótesis por orden: (1) los pitidos de
  diagnóstico/zumbido mezclados a escala alta y las transiciones underflow<->audio; (2) frames
  descartados por falta de heap (cortes) y reinicios de stream; (3) el framework convierte 16->24 bits
  con una longitud distinta de la que asumimos (`len/2`); (4) CPU.
- Siguiente paso: capturar el stream LDAC real (PipeWire, calidad HQ y ABR) y pasarlo por
  `emu_decoder.py`, con el framework simulado más fiel (tamaños de petición reales); quitar todos los
  tonos de diagnóstico del build normal (silencio en vez de tono) y limitar la amplitud ante errores.
  Nada de pruebas con los cascos puestos.

### Framework de decoders de la stock (más nuevo que OpenPineBuds)

El `init` SBC del stock (`0x00201330`, overlay) reserva un pool de 250 frames de `0x80` B en una lista y
usa: `0x0027B340` = `a2dp_audio_heap_malloc` (RAMX de `0x2007B340`), `0x002640A4` traza,
`0x00263B8C` assert, `0x00260228` memset, y funciones de lista dentro del overlay (`0x00201AD8`,
`0x00201A98`, `0x00201A88`, `0x002019F8`, `0x00201A58`, `0x00201A68` lastframe_info_set?).
Contexto `*0x00201454`: `+0x16` dest_packet_mut (<0xFA), `+0x90` lista, `+0x117` flag.
Memoria de `ldac_lib`: todo vía `calloc_ldac` -> `syspool_get_buff` (pool de audio, se reinicia en cada
sesión): handle `0x1080` + `0x50` + bloques `0xACC` + `0x128`/canal ≈ 10–12 KB (AAC ya usa más del
mismo pool). El `init` propio debe comprobar `syspool_free_size()` y fallar limpio (sin assert).
`syspool_get_buff`: función en RAM cerca de `0x20072030` (literal en fichero `0xB2EC`); falta fijar
`syspool_free_size`.

Funciones del framework identificadas (flash, todas con el mutex del contexto `+0xb0`; desde el
overlay se llega por *veneers* `push r0; ldr r0,=dst; mov r12,r0; pop r0; bx r12` en
`0x002019F8..0x00201B08`):

| Flash | Identificación | Evidencia |
|---|---|---|
| `0x0027B340` (RAMX) | `a2dp_audio_heap_malloc` (**ASSERT si no hay memoria**) | cadena; recorre heaps y hace assert |
| `0x0027B2E4` (RAMX) | `a2dp_audio_heap_free` | cadena |
| `0x0C126BC4` | `a2dp_audio_list_length` | lock; `0x0C12B05C(list)`; unlock |
| `0x0C126BF0` | `a2dp_audio_list_front` | `length ? 0x0C12B074(list) : 0` (igual que el SDK) |
| `0x0C126C5C`/`0x0C126C8C`/`0x0C126CBC` | append / remove / ? `(list, data)` | por emparejar |
| `0x0C126C30` | `(list)`: clear/free/back | por emparejar |
| `0x0C127D68` | `a2dp_audio_decoder_internal_lastframe_info_set` | copia el struct a un global |
| `0x0C1132A4` | `app_bt_stream_get_dma_buffer_samples` | el reproductor hace `>>1` -> frame_samples |
| `0x00263B8C` (RAMX) | assert (no retorna) | `"%s MTU OVERFLOW"` en SBC init |

**Pendiente**: emparejar cada función del SBC stock con `a2dp_decoder_sbc.cpp` del SDK y escribir el
decoder LDAC contra estas direcciones (las del overlay no sirven: hay que usar las del framework).

### Herramientas

- `tools/mkelf.sh`: ELF de la stock con todas las regiones en su dirección de ejecución (+overlay SBC).
- `tools/decomp.sh OUT ADDR...` / `tools/xrefs.sh ADDR...`: Ghidra headless sobre
  `ghidra/stock65` (importado con `NoNonReturn.java`: Ghidra marcaba la traza `0x0C13B4B8`
  como "no retorna").
- `ldac/`: `Makefile` (clang + ld.lld, Cortex-M33 hard-float), `link.ld` (blob en `0x0C1B7090`,
  `.data/.bss` en el hueco `0x200BC738`), `stock_syms.ld` (símbolos de la stock con evidencia).
  `make probe`: decoder LDAC completo = 21.656 B texto, 96 B data, 32 B bss.

## Pendiente (fase 2)

3. RE del callback A2DP `0x0C0BFBC9`: dónde se decide el tipo de códec y el sample rate.
4. RE de `a2dp_audio_init` (switch) y de `A2DP_AUDIO_DECODER_T` en la stock (comparar con el
   decoder AAC: `a2dp_audio_aac_lc_init` `0x46F74`).
5. Toolchain: clang `--target=thumbv7em-none-eabi` + `ld.lld`, script de enlace con las
   direcciones stock como símbolos; código en la flash libre (antes del trailer, moviendo
   `build_info_start` de la cabecera, o detrás con copia del trailer al final).
6. **Vía de recuperación por PC: HECHO** (`tools/pc_ota3.py`, validado con la SBC-XQ v2, 57 s, flags 0). Antes: arreglar `pc_ota2.py` para copiar exactamente el flujo de la app
   (paquetes de 660 B, esperar `0x83` tras cada `0x82`, `0x90 00`), por si un fallo en el camino
   LDAC impidiera usar el móvil. En el móvil: desactivar "Audio HD"/códecs opcionales para los
   Fairbuds antes de probar.
7. Gate antes de cada flasheo: `besfw.py check`, diff que demuestre que arranque/OTA no cambian,
   APK de restauración stock a mano.
