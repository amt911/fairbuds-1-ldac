/* Payload glue (runs from RAM, see ldac_ram.h): the export table the flash decoder reads, and
 * ldac_lib's allocator hooks forwarded to the flash side through the import table. */
#include <stdint.h>
#include "ldac_ram.h"

struct ldac_ram_imports ldac_ram_imports;

uint32_t syspool_free_size(void) { return ldac_ram_imports.free_size(); }
int syspool_get_buff(uint8_t **buff, uint32_t size) { return ldac_ram_imports.get_buff(buff, size); }

__attribute__((section(".exports"), used))
const struct ldac_ram_exports ldac_ram_exports = {
    LDAC_RAM_MAGIC, ldacBT_get_handle, ldacBT_init_handle_decode, ldacBT_decode, ldacBT_free_handle,
    &ldac_ram_imports,
};
