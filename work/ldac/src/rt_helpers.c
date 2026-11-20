/* Runtime helpers that ldac_lib.a needs and the stock firmware does not provide.
 * Pure integer bit manipulation: no floating-point instructions, no libgcc calls. */
#include <stdint.h>
#include <stddef.h>

typedef union { double d; uint64_t u; } dbits;

/* floor() for IEEE-754 binary64. The bits go through an empty asm so clang cannot turn the
 * integer tests back into double compares (which would need __aeabi_dcmp* on this FPU). */
double floor(double x)
{
    dbits v = { .d = x };
    uint32_t hi = (uint32_t)(v.u >> 32), lo = (uint32_t)v.u;
    __asm__("" : "+r"(hi), "+r"(lo));
    int e = (int)((hi >> 20) & 0x7ff) - 1023;
    if (e >= 52)                                   /* integral already, or inf/nan */
        return x;
    if (e < 0) {                                   /* |x| < 1 */
        if (((hi & 0x7fffffffu) | lo) == 0) return x;  /* +-0 */
        hi = (hi >> 31) ? 0xBFF00000u : 0;         /* -1.0 : +0.0 */
        lo = 0;
    } else {
        uint64_t u = ((uint64_t)hi << 32) | lo;
        uint64_t frac = 0x000FFFFFFFFFFFFFull >> e;
        if ((u & frac) == 0) return x;             /* integral */
        if (hi >> 31) u += frac;                   /* negative: round toward -inf */
        u &= ~frac;
        hi = (uint32_t)(u >> 32); lo = (uint32_t)u;
    }
    v.u = ((uint64_t)hi << 32) | lo;
    return v.d;
}

/* double -> int64, truncating toward zero (C semantics, saturating like libgcc). */
long long __aeabi_d2lz(double x)
{
    dbits v = { .d = x };
    int neg = (int)(v.u >> 63);
    int e = (int)((v.u >> 52) & 0x7ff) - 1023;
    if (e < 0) return 0;
    if (e >= 63) return neg ? INT64_MIN : INT64_MAX;
    uint64_t m = (v.u & 0x000FFFFFFFFFFFFFull) | 0x0010000000000000ull;
    uint64_t r = (e >= 52) ? (m << (e - 52)) : (m >> (52 - e));
    return neg ? -(long long)r : (long long)r;
}

void *memcpy(void *dst, const void *src, size_t n)
{
    uint8_t *d = dst; const uint8_t *s = src;
    while (n--) *d++ = *s++;
    return dst;
}

void *memset(void *dst, int c, size_t n)
{
    uint8_t *d = dst;
    while (n--) *d++ = (uint8_t)c;
    return dst;
}
