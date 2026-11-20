/* Link probe: pulls the whole LDAC decoder in, to measure size and list unresolved symbols. */
#include "ldacBT.h"
__attribute__((section(".entry"), used)) int link_probe(unsigned char *in, int n, unsigned char *pcm)
{
    HANDLE_LDAC_BT h = ldacBT_get_handle();
    if (!h) return -1;
    ldacBT_init_handle_decode(h, 0, 44100, 0, 0, 0);
    int used = 0, wrote = 0;
    ldacBT_decode(h, in, pcm, LDACBT_SMPL_FMT_S16, n, &used, &wrote);
    ldacBT_free_handle(h);
    return wrote;
}
