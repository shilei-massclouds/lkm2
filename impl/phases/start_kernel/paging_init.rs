//! Implemented PagingInit prefix through `setup_bootmem`.

use crate::objects::dtb_blob::DtbBlob;
use crate::objects::memblock::{MemBlock, MemBlockError, MemBlockMemory};

pub(crate) fn setup_bootmem(
    memory: MemBlockMemory,
    dtb: &DtbBlob<'_>,
    dtb_physical_address: u64,
    kernel_image: (u64, u64),
) -> Result<MemBlock, MemBlockError> {
    MemBlock::setup_bootmem(memory, dtb, dtb_physical_address, kernel_image)
}
