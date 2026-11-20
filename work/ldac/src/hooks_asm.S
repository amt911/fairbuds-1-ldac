/* LDAC add-on: register-exact trampolines for hooks H2 and H4 (see hooks.c). */
    .syntax unified
    .cpu cortex-m33
    .thumb
    .text

/* H2: replaces `cmp.w r8, #0` at 0x0C0C027A (followed by `bne 0x0C0C0318`).
 * In: r8 = codec type, r3 = codec struct ptr, r4 = device context. Every register is preserved;
 * the flags are the result of `cmp r8, #0`, exactly like the original instruction. */
    .global ldac_cfg_hook
    .type ldac_cfg_hook, %function
    .thumb_func
ldac_cfg_hook:
    cmp     r8, #0xff
    beq     2f
    cmp     r8, #0
    bne     1f
2:
    push    {r0-r3, r12, lr}
    mov     r0, r3
    mov     r1, r4
    bl      ldac_cfg_apply
    pop     {r0-r3, r12, lr}
1:  cmp     r8, #0
    bx      lr
    .size ldac_cfg_hook, . - ldac_cfg_hook

/* H4: replaces `cmp r3, #2 ; bne.w 0x0C0C0318` at 0x0C0C00AC (6 bytes -> bl + nop).
 * In: r3 = remote codec type (never 0 here: SBC branched away before), r2 = remote codec caps,
 * r8 = local config codec struct, lr = 0x0C0C00B1. AAC -> 0x0C0C00B2 (lr + 2, skips the nop);
 * anything else -> 0x0C0C0318 after filling an LDAC config when it is Sony LDAC. */
    .global ldac_cfginit_hook
    .type ldac_cfginit_hook, %function
    .thumb_func
ldac_cfginit_hook:
    cmp     r3, #2
    bne     1f
    add     lr, lr, #2
    bx      lr
1:  push    {r0-r3, r12, lr}
    cmp     r3, #0xff
    bne     2f
    mov     r0, r2
    mov     r1, r8
    bl      ldac_cfginit_apply
2:  pop     {r0-r3, r12, lr}
    ldr     lr, =0x0C0C0319
    bx      lr
    .size ldac_cfginit_hook, . - ldac_cfginit_hook
    .ltorg

/* H5: replaces `ldrb r3, [r5] ; cmp r3, #2` at 0x0C0BFCA8 in the A2DP stream OPEN handler (events
 * 2/0x1f), where both SBC-branch paths join after the stock stored the codec as SBC (it treats every
 * non-AAC codec as SBC: +0x4a=0, +0x3f=elements[0]&0xF0, +0x42 from bit3).
 * In: r5 = event params ([r5] event, [r5+0xc] codec struct), r4 = device context.
 * Out: r3 = [r5] and flags of `cmp r3, #2`, exactly like the two original instructions; every other
 * register preserved. For Sony LDAC the device context is corrected by ldac_cfg_apply. */
    .global ldac_open_hook
    .type ldac_open_hook, %function
    .thumb_func
ldac_open_hook:
    push    {r0-r3, r12, lr}
    ldr     r0, [r5, #0xc]
    mov     r1, r4
    bl      ldac_cfg_apply
    pop     {r0-r3, r12, lr}
    ldrb    r3, [r5]
    cmp     r3, #2
    bx      lr
    .size ldac_open_hook, . - ldac_open_hook

/* H6: replaces `subs r3, #0x80 ; cmp r3, #0x19` at 0x0C0AD072 in the OTA command handler
 * (FUN_0c0ad038, r4 = received packet, r6 = length), just before `bhi 0x0C0AD132` (unknown opcode:
 * the function epilogue) and the tbh opcode table. In: r3 = opcode byte.
 * Opcode 0xA5 (LDAC statistics request, unused by the stock: 0x80..0x99 only) calls
 * ldac_dbg_request(packet, length) and leaves r3/flags as for opcode 0xFF, so the stock takes its
 * unknown-opcode exit. Every other opcode gets exactly the two original instructions. */
    .global ldac_dbg_hook
    .type ldac_dbg_hook, %function
    .thumb_func
ldac_dbg_hook:
    cmp     r3, #0xa5
    bne     1f
    push    {r0-r3, r12, lr}
    mov     r0, r4
    mov     r1, r6
    bl      ldac_dbg_request
    pop     {r0-r3, r12, lr}
    movs    r3, #0xff
1:  subs    r3, #0x80
    cmp     r3, #0x19
    bx      lr
    .size ldac_dbg_hook, . - ldac_dbg_hook
