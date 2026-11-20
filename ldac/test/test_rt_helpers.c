/* Host test: compare rt_helpers against the C library on edge cases + random values. */
#include <math.h>
#include <stdio.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#define floor my_floor
#define __aeabi_d2lz my_d2lz
#define memcpy my_memcpy
#define memset my_memset
#include "../src/rt_helpers.c"
#undef floor
#undef memcpy
#undef memset
static long long ref_d2lz(double x){ if (isnan(x)) return 0; if (x >= 9.2233720368547758e18) return INT64_MAX; if (x <= -9.2233720368547758e18) return INT64_MIN; return (long long)x; }
int main(void){
    double cases[] = {0.0,-0.0,0.5,-0.5,1.0,-1.0,1.5,-1.5,2.0,-2.0,123.999,-123.999,1e15+0.5,-1e15-0.5,
                      4503599627370495.5,-4503599627370495.5,1e300,-1e300,0.999999,-0.999999,1e-300,-1e-300,
                      9.2e18,-9.2e18,1e19,-1e19, 3.0e9, -3.0e9};
    int bad = 0, n = 0;
    for (size_t i = 0; i < sizeof cases/sizeof cases[0]; i++) {
        double x = cases[i];
        double a = my_floor(x), b = floor(x);
        if (memcmp(&a,&b,8)) { printf("floor(%.17g): got %.17g want %.17g\n", x, a, b); bad++; }
        if (my_d2lz(x) != ref_d2lz(x)) { printf("d2lz(%.17g): got %lld want %lld\n", x, my_d2lz(x), ref_d2lz(x)); bad++; }
        n += 2;
    }
    srand(1);
    for (int i = 0; i < 2000000; i++) {
        uint64_t u = ((uint64_t)rand() << 33) ^ ((uint64_t)rand() << 11) ^ (uint64_t)rand();
        double x; memcpy(&x, &u, 8);
        if (isnan(x)) continue;
        double a = my_floor(x), b = floor(x);
        if (memcmp(&a,&b,8)) { if (bad < 10) printf("floor(%a): got %a want %a\n", x, a, b); bad++; }
        if (my_d2lz(x) != ref_d2lz(x)) { if (bad < 10) printf("d2lz(%a): got %lld want %lld\n", x, my_d2lz(x), ref_d2lz(x)); bad++; }
        n += 2;
    }
    char s[64], d1[64], d2[64]; for (int i=0;i<64;i++) s[i]=(char)i;
    my_memcpy(d1, s, 64); memcpy(d2, s, 64); if (memcmp(d1,d2,64)) { puts("memcpy bad"); bad++; }
    my_memset(d1, 0x5a, 33); memset(d2, 0x5a, 33); if (memcmp(d1,d2,64)) { puts("memset bad"); bad++; }
    printf("%d checks, %d failures\n", n + 2, bad);
    return bad != 0;
}
