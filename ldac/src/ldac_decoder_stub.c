/* Placeholder decoder: never selected (ready = 0). Lets the hooks be built and emulated before the
 * real decoder exists. Replaced by ldac_decoder.c. */
#include "addon.h"
static const uint32_t no_table[28];
const void *ldac_decoder_table(void) { return no_table; }
int ldac_decoder_table_ready(void) { return 0; }
