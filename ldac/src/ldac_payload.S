/* LDAC RAM payload image (tools/mkpayload.py output), copied and relocated by ldac_decoder.c. */
    .section .rodata.ldac_payload, "a"
    .balign 4
    .global ldac_payload
ldac_payload:
    .incbin "build/ldac_ram.img"
    .balign 4
    .global ldac_payload_end
ldac_payload_end:
