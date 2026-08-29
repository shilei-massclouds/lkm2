//! MMU-off early virtual-memory setup.

use core::arch::asm;
use core::mem::{align_of, size_of};
use core::sync::atomic::{AtomicU32, AtomicU64, Ordering};

use crate::checkpoint::swapper as swapper_checkpoints;
use crate::checkpoint::swapper::SwapperObservation;
use crate::checkpoint::swapper_content as swapper_content_checkpoints;
use crate::checkpoint::swapper_content::SwapperContentObservation;
use crate::checkpoint::vm as checkpoints;
use crate::checkpoint::vm::{
    EarlyDtbObservation, EarlyKernelObservation, KernelMapObservation, TrampolineObservation,
};
use crate::config::{
    FIX_FDT_VA_SV39, FIX_FDT_VA_SV48, FIX_FDT_VA_SV57, KERNEL_LINK_ADDR, PAGE_OFFSET_SV39,
    PAGE_OFFSET_SV48, PAGE_OFFSET_SV57, PAGE_SHIFT,
};
use crate::objects::dtb_blob::EarlyDtbMapping;
use crate::objects::memblock::{MemBlock, MemBlockError};

const PAGE_SIZE: usize = 1 << PAGE_SHIFT;
const PAGE_TABLE_ENTRIES: usize = PAGE_SIZE / size_of::<u64>();
const PAGE_TABLE_INDEX_MASK: usize = PAGE_TABLE_ENTRIES - 1;

const PMD_SHIFT: usize = 21;
const PMD_SIZE: usize = 1 << PMD_SHIFT;
const PMD_MASK: usize = !(PMD_SIZE - 1);
const PUD_SHIFT: usize = 30;
const P4D_SHIFT: usize = 39;
const PGD_SHIFT_SV57: usize = 48;
const PUD_SIZE: usize = 1 << PUD_SHIFT;

const PTE_VALID: u64 = 1 << 0;
const PTE_READ: u64 = 1 << 1;
const PTE_WRITE: u64 = 1 << 2;
const PTE_EXEC: u64 = 1 << 3;
const PTE_USER: u64 = 1 << 4;
const PTE_GLOBAL: u64 = 1 << 5;
const PTE_ACCESSED: u64 = 1 << 6;
const PTE_DIRTY: u64 = 1 << 7;
const PTE_PERMISSION_MASK: u64 =
    PTE_READ | PTE_WRITE | PTE_EXEC | PTE_USER | PTE_GLOBAL | PTE_ACCESSED | PTE_DIRTY;
const PTE_PPN_BITS: u32 = 44;
const PTE_PPN_MASK: u64 = (1_u64 << PTE_PPN_BITS) - 1;
const PTE_LOW_BITS_MASK: u64 = (1_u64 << 10) - 1;

const PAGE_TABLE_FLAGS: u64 = PTE_VALID;
const PAGE_KERNEL_EXEC_FLAGS: u64 =
    PTE_VALID | PTE_READ | PTE_WRITE | PTE_EXEC | PTE_GLOBAL | PTE_ACCESSED | PTE_DIRTY;
const PAGE_KERNEL_FLAGS: u64 =
    PTE_VALID | PTE_READ | PTE_WRITE | PTE_GLOBAL | PTE_ACCESSED | PTE_DIRTY;

const SATP_MODE_SHIFT: u32 = 60;
const SATP_PPN_MASK: u64 = (1_u64 << 44) - 1;

const ADDRESS_SPACE_LAST_PAGE: usize = usize::MAX - (PAGE_SIZE - 1);

#[derive(Clone, Copy, PartialEq, Eq)]
#[repr(u8)]
enum PagingMode {
    Sv39 = 8,
    Sv48 = 9,
    Sv57 = 10,
}

impl PagingMode {
    const fn satp_bits(self) -> u64 {
        (self as u64) << SATP_MODE_SHIFT
    }

    const fn from_satp_bits(value: u64) -> Option<Self> {
        if value == Self::Sv57.satp_bits() {
            Some(Self::Sv57)
        } else if value == Self::Sv48.satp_bits() {
            Some(Self::Sv48)
        } else if value == Self::Sv39.satp_bits() {
            Some(Self::Sv39)
        } else {
            None
        }
    }

    const fn top_level_shift(self) -> usize {
        match self {
            Self::Sv57 => PGD_SHIFT_SV57,
            Self::Sv48 => P4D_SHIFT,
            Self::Sv39 => PUD_SHIFT,
        }
    }

    const fn virtual_address_bits(self) -> u32 {
        match self {
            Self::Sv57 => 57,
            Self::Sv48 => 48,
            Self::Sv39 => 39,
        }
    }

    const fn page_offset(self) -> usize {
        match self {
            Self::Sv57 => PAGE_OFFSET_SV57,
            Self::Sv48 => PAGE_OFFSET_SV48,
            Self::Sv39 => PAGE_OFFSET_SV39,
        }
    }

    const fn fix_fdt_va(self) -> usize {
        match self {
            Self::Sv57 => FIX_FDT_VA_SV57,
            Self::Sv48 => FIX_FDT_VA_SV48,
            Self::Sv39 => FIX_FDT_VA_SV39,
        }
    }
}

#[derive(Clone, Copy)]
#[repr(u32)]
pub(crate) enum VmSetupError {
    UnsupportedPagingMode = 1,
    InvalidAlignment = 2,
    AddressOverflow = 3,
    PageTableCapacityExceeded = 4,
    InvalidMappingRange = 5,
}

type VmResult<T> = Result<T, VmSetupError>;

// SAFETY: this is the sole linker-visible definition of `satp_mode`. Its
// atomic word is written only by the boot hart after probing and contains the
// complete SATP MODE bit field derived from `PagingMode`.
#[unsafe(export_name = "satp_mode")]
pub(crate) static SATP_MODE: AtomicU64 = AtomicU64::new(0);

fn selected_paging_mode() -> VmResult<PagingMode> {
    PagingMode::from_satp_bits(SATP_MODE.load(Ordering::Acquire))
        .ok_or(VmSetupError::UnsupportedPagingMode)
}

#[derive(Clone, Copy)]
struct Pte(u64);

impl Pte {
    fn table(page_pa: usize) -> VmResult<Self> {
        if page_pa & (PAGE_SIZE - 1) != 0 {
            return Err(VmSetupError::InvalidAlignment);
        }
        Self::from_pa_and_flags(page_pa, PAGE_TABLE_FLAGS)
    }

    fn leaf_2m(page_pa: usize, flags: u64) -> VmResult<Self> {
        if page_pa & (PMD_SIZE - 1) != 0 {
            return Err(VmSetupError::InvalidAlignment);
        }
        if flags & PTE_VALID == 0
            || flags & (PTE_READ | PTE_WRITE | PTE_EXEC) == 0
            || flags & PTE_USER != 0
            || flags & !((1 << 8) - 1) != 0
        {
            return Err(VmSetupError::InvalidMappingRange);
        }
        Self::from_pa_and_flags(page_pa, flags)
    }

    fn leaf_1g(page_pa: usize, flags: u64) -> VmResult<Self> {
        if page_pa & (PUD_SIZE - 1) != 0 {
            return Err(VmSetupError::InvalidAlignment);
        }
        Self::leaf(page_pa, flags)
    }

    fn leaf_4k(page_pa: usize, flags: u64) -> VmResult<Self> {
        if page_pa & (PAGE_SIZE - 1) != 0 {
            return Err(VmSetupError::InvalidAlignment);
        }
        Self::leaf(page_pa, flags)
    }

    fn leaf(page_pa: usize, flags: u64) -> VmResult<Self> {
        if flags & PTE_VALID == 0
            || flags & (PTE_READ | PTE_WRITE | PTE_EXEC) == 0
            || flags & PTE_USER != 0
            || flags & !((1 << 8) - 1) != 0
        {
            return Err(VmSetupError::InvalidMappingRange);
        }
        Self::from_pa_and_flags(page_pa, flags)
    }

    fn from_pa_and_flags(page_pa: usize, flags: u64) -> VmResult<Self> {
        let ppn = (page_pa as u64) >> PAGE_SHIFT;
        if ppn & !PTE_PPN_MASK != 0 || flags & !PTE_PERMISSION_MASK != PTE_VALID {
            return Err(VmSetupError::AddressOverflow);
        }
        Ok(Self((ppn << 10) | flags))
    }

    fn physical_address(self) -> u64 {
        ((self.0 >> 10) & PTE_PPN_MASK) << PAGE_SHIFT
    }

    fn flags(self) -> u64 {
        self.0 & PTE_LOW_BITS_MASK
    }
}

#[repr(C, align(4096))]
struct PageTablePage {
    entries: [AtomicU64; PAGE_TABLE_ENTRIES],
}

impl PageTablePage {
    const fn new() -> Self {
        Self {
            entries: [const { AtomicU64::new(0) }; PAGE_TABLE_ENTRIES],
        }
    }

    fn clear(&self) {
        for entry in &self.entries {
            entry.store(0, Ordering::Relaxed);
        }
    }

    fn install(&self, index: usize, pte: Pte) -> VmResult<()> {
        let Some(entry) = self.entries.get(index) else {
            return Err(VmSetupError::PageTableCapacityExceeded);
        };
        let current = entry.load(Ordering::Relaxed);
        if current != 0 && current != pte.0 {
            return Err(VmSetupError::InvalidMappingRange);
        }
        entry.store(pte.0, Ordering::Relaxed);
        Ok(())
    }

    fn read(&self, index: usize) -> Option<Pte> {
        let value = self.entries.get(index)?.load(Ordering::Acquire);
        (value != 0).then_some(Pte(value))
    }

    fn physical_address(&self) -> usize {
        self as *const Self as usize
    }
}

#[repr(C)]
struct PathPages {
    level4: PageTablePage,
    level3: PageTablePage,
    level2: PageTablePage,
}

impl PathPages {
    const fn new() -> Self {
        Self {
            level4: PageTablePage::new(),
            level3: PageTablePage::new(),
            level2: PageTablePage::new(),
        }
    }

    fn clear(&self) {
        self.level4.clear();
        self.level3.clear();
        self.level2.clear();
    }
}

#[repr(C)]
pub(crate) struct NoFixmap;

#[repr(C)]
struct FixmapPages {
    path: PathPages,
}

impl FixmapPages {
    const fn new() -> Self {
        Self {
            path: PathPages::new(),
        }
    }
}

#[repr(C)]
pub(crate) struct PageTableType<F> {
    root: PageTablePage,
    kernel_path: PathPages,
    fixmap: F,
}

impl<F> PageTableType<F> {
    const fn new(fixmap: F) -> Self {
        Self {
            root: PageTablePage::new(),
            kernel_path: PathPages::new(),
            fixmap,
        }
    }

    fn clear_kernel(&self) {
        self.root.clear();
        self.kernel_path.clear();
    }

    fn root_satp_ppn(&self) -> VmResult<u64> {
        let address = self.root.physical_address();
        if address & (PAGE_SIZE - 1) != 0 {
            return Err(VmSetupError::InvalidAlignment);
        }
        let ppn = (address as u64) >> PAGE_SHIFT;
        if ppn & !SATP_PPN_MASK != 0 {
            return Err(VmSetupError::AddressOverflow);
        }
        Ok(ppn)
    }

    fn map_kernel_2m(
        &self,
        mode: PagingMode,
        virtual_address: usize,
        physical_address: usize,
        flags: u64,
    ) -> VmResult<()> {
        map_2m(
            &self.root,
            &self.kernel_path,
            mode,
            virtual_address,
            physical_address,
            flags,
        )
    }

    fn observe_kernel_leaf(&self, mode: PagingMode, virtual_address: usize) -> (Option<Pte>, bool) {
        observe_leaf_2m(&self.root, &self.kernel_path, mode, virtual_address)
    }
}

impl PageTableType<NoFixmap> {
    const fn new_trampoline() -> Self {
        Self::new(NoFixmap)
    }

    fn preset(&self) {
        self.clear_kernel();
    }
}

impl PageTableType<FixmapPages> {
    const fn new_early() -> Self {
        Self::new(FixmapPages::new())
    }

    fn preset(&self) {
        self.clear_all();
    }

    fn clear_all(&self) {
        self.clear_kernel();
        self.fixmap.path.clear();
    }

    fn map_fixmap_2m(
        &self,
        mode: PagingMode,
        virtual_address: usize,
        physical_address: usize,
        flags: u64,
    ) -> VmResult<()> {
        map_2m(
            &self.root,
            &self.fixmap.path,
            mode,
            virtual_address,
            physical_address,
            flags,
        )
    }
}

#[repr(C)]
struct SwapperPathPages {
    level4: PageTablePage,
    level3: PageTablePage,
    level2: PageTablePage,
    level1: PageTablePage,
}

impl SwapperPathPages {
    const fn new() -> Self {
        Self {
            level4: PageTablePage::new(),
            level3: PageTablePage::new(),
            level2: PageTablePage::new(),
            level1: PageTablePage::new(),
        }
    }

    fn clear(&self) {
        self.level4.clear();
        self.level3.clear();
        self.level2.clear();
        self.level1.clear();
    }
}

#[repr(C, align(4096))]
pub(crate) struct SwapperPageTableType {
    root: PageTablePage,
    linear_path: SwapperPathPages,
    kernel_path: SwapperPathPages,
    fixmap_path: SwapperPathPages,
    mode: AtomicU64,
    fixmap_va: AtomicU64,
    linear_va: AtomicU64,
    linear_pa: AtomicU64,
    linear_flags: AtomicU64,
    kernel_va: AtomicU64,
    kernel_pa: AtomicU64,
    kernel_flags: AtomicU64,
    fixmap_cleared: AtomicU64,
    satp_switched: AtomicU64,
    tlb_flush_completed: AtomicU64,
    late_mode_selected: AtomicU64,
}

impl SwapperPageTableType {
    const fn new() -> Self {
        Self {
            root: PageTablePage::new(),
            linear_path: SwapperPathPages::new(),
            kernel_path: SwapperPathPages::new(),
            fixmap_path: SwapperPathPages::new(),
            mode: AtomicU64::new(0),
            fixmap_va: AtomicU64::new(0),
            linear_va: AtomicU64::new(0),
            linear_pa: AtomicU64::new(0),
            linear_flags: AtomicU64::new(0),
            kernel_va: AtomicU64::new(0),
            kernel_pa: AtomicU64::new(0),
            kernel_flags: AtomicU64::new(0),
            fixmap_cleared: AtomicU64::new(0),
            satp_switched: AtomicU64::new(0),
            tlb_flush_completed: AtomicU64::new(0),
            late_mode_selected: AtomicU64::new(0),
        }
    }

    fn clear(&self) {
        self.root.clear();
        self.linear_path.clear();
        self.kernel_path.clear();
        self.fixmap_path.clear();
        self.fixmap_cleared.store(0, Ordering::Relaxed);
        self.satp_switched.store(0, Ordering::Relaxed);
        self.tlb_flush_completed.store(0, Ordering::Relaxed);
        self.late_mode_selected.store(0, Ordering::Relaxed);
    }

    fn observation(&self) -> SwapperObservation {
        SwapperObservation {
            mode: self.mode.load(Ordering::Acquire),
            fixmap_va: self.fixmap_va.load(Ordering::Acquire),
            linear_va: self.linear_va.load(Ordering::Acquire),
            linear_pa: self.linear_pa.load(Ordering::Acquire),
            linear_flags: self.linear_flags.load(Ordering::Acquire),
            kernel_va: self.kernel_va.load(Ordering::Acquire),
            kernel_pa: self.kernel_pa.load(Ordering::Acquire),
            kernel_flags: self.kernel_flags.load(Ordering::Acquire),
            fixmap_cleared: self.fixmap_cleared.load(Ordering::Acquire),
            satp_switched: self.satp_switched.load(Ordering::Acquire),
            tlb_flush_completed: self.tlb_flush_completed.load(Ordering::Acquire),
            late_mode_selected: self.late_mode_selected.load(Ordering::Acquire),
        }
    }

    fn content_observation(
        &self,
        memblock: &MemBlock,
        mode: PagingMode,
    ) -> SwapperContentObservation {
        let (fixmap_valid, fixmap_count, fixmap_lo, fixmap_hi) = self.digest_window(
            mode,
            mode.fix_fdt_va(),
            2 * PMD_SIZE,
            1,
            PTE_VALID | PTE_PERMISSION_MASK,
        );
        let mut linear = ContentDigest::new(2);
        let mut linear_valid = true;
        let linear_physical_base = memblock
            .memory_ranges()
            .next()
            .and_then(|(base, _limit)| usize::try_from(base).ok())
            .map(|base| base & PMD_MASK);
        let linear_mappable_start = linear_mappable_physical_start(memblock);
        if let (Some(linear_base), Some(mappable_start)) =
            (linear_physical_base, linear_mappable_start)
        {
            for (base, end) in memblock.memory_ranges() {
                let Ok(memory_start) = usize::try_from(base) else {
                    linear_valid = false;
                    continue;
                };
                let Ok(limit) = usize::try_from(end) else {
                    linear_valid = false;
                    continue;
                };
                // Linux excludes the leading firmware-owned NOMAP reservation
                // from for_each_mem_range().  The Rust MemBlock deliberately
                // keeps the older implementation-independent reservation
                // schema, so derive the same linear-map projection here.
                let start = core::cmp::max(memory_start, mappable_start);
                if start >= limit {
                    continue;
                }
                let Some(size) = limit.checked_sub(start) else {
                    linear_valid = false;
                    continue;
                };
                let virtual_start = start
                    .checked_sub(linear_base)
                    .and_then(|offset| mode.page_offset().checked_add(offset));
                let count_before = linear.count;
                let (valid, count) = self.digest_into(
                    mode,
                    &mut linear,
                    ContentRange {
                        virtual_start,
                        physical_start: Some(start),
                        size,
                        flags_mask: PTE_VALID | PTE_READ,
                        required_flags: PTE_VALID | PTE_READ,
                        forbidden_flags: PTE_USER,
                        path: &self.linear_path,
                    },
                );
                linear_valid &= valid;
                if count == count_before && size != 0 {
                    linear_valid = false;
                }
            }
        } else {
            linear_valid = false;
        }
        linear_valid &= linear.count != 0;
        let linear_count = linear.count;
        let (linear_lo, linear_hi) = linear.finish();
        let kernel_valid = self.kernel_walk_valid(mode);
        SwapperContentObservation {
            fixmap_valid,
            fixmap_count,
            fixmap_digest_lo: fixmap_lo,
            fixmap_digest_hi: fixmap_hi,
            linear_valid: u64::from(linear_valid),
            linear_count,
            linear_digest_lo: linear_lo,
            linear_digest_hi: linear_hi,
            kernel_walk_valid: u64::from(kernel_valid),
        }
    }

    fn digest_window(
        &self,
        mode: PagingMode,
        virtual_start: usize,
        size: usize,
        class: u8,
        flags_mask: u64,
    ) -> (u64, u64, u64, u64) {
        let mut digest = ContentDigest::new(class);
        let (valid, _count) = self.digest_into(
            mode,
            &mut digest,
            ContentRange {
                virtual_start: Some(virtual_start),
                physical_start: None,
                size,
                flags_mask,
                required_flags: PTE_VALID,
                forbidden_flags: 0,
                path: &self.fixmap_path,
            },
        );
        let count = digest.count;
        let (lo, hi) = digest.finish();
        (u64::from(valid), count, lo, hi)
    }

    fn digest_into(
        &self,
        mode: PagingMode,
        digest: &mut ContentDigest,
        range: ContentRange<'_>,
    ) -> (bool, u64) {
        let Some(mut va) = range.virtual_start else {
            return (false, 0);
        };
        let end = match va.checked_add(range.size) {
            Some(end) => end,
            None => return (false, 0),
        };
        let mut pa = range.physical_start;
        let mut valid = true;
        while va < end {
            let expected_pa = pa;
            let entry = lookup_path(&self.root, range.path, mode, va);
            let (entry_pa, entry_flags) = match entry {
                Some((pte, leaf_size)) => (
                    pte.physical_address() as usize + (va & (leaf_size - 1)),
                    pte.flags(),
                ),
                None => {
                    valid = false;
                    (0, 0)
                }
            };
            if let Some(expected) = expected_pa
                && entry_pa != expected
            {
                valid = false;
            }
            if entry_flags & range.required_flags != range.required_flags
                || entry_flags & range.forbidden_flags != 0
            {
                valid = false;
            }
            digest.push(va as u64, entry_pa as u64, entry_flags & range.flags_mask);
            va = match va.checked_add(PAGE_SIZE) {
                Some(next) => next,
                None => return (false, digest.count),
            };
            if let Some(current) = pa {
                pa = current.checked_add(PAGE_SIZE);
                if pa.is_none() && va < end {
                    valid = false;
                }
            }
        }
        (valid, digest.count)
    }

    fn kernel_walk_valid(&self, mode: PagingMode) -> bool {
        let start = VM.kernel_map.virtual_address();
        let size = VM.kernel_map.image_size();
        if size == 0 || start & (PAGE_SIZE - 1) != 0 {
            return false;
        }
        let Some(end) = start.checked_add(size) else {
            return false;
        };
        let mut va = start;
        while va < end {
            let Some((pte, leaf_size)) = lookup_path(&self.root, &self.kernel_path, mode, va)
            else {
                return false;
            };
            let Some(expected_pa) = VM.kernel_map.physical_address().checked_add(va - start) else {
                return false;
            };
            if pte.physical_address() as usize + (va & (leaf_size - 1)) != expected_pa
                || !valid_leaf_flags(pte.flags())
                || pte.flags() & PTE_USER != 0
                || pte.flags() & PTE_WRITE != 0 && pte.flags() & PTE_EXEC != 0
            {
                return false;
            }
            va = match va.checked_add(PAGE_SIZE) {
                Some(next) => next,
                None => return false,
            };
        }
        true
    }
}

const CONTENT_FNV_PRIME: u64 = 0x0000_0100_0000_01b3;
const CONTENT_FNV_OFFSET_LO: u64 = 0xcbf2_9ce4_8422_2325;
const CONTENT_FNV_OFFSET_HI: u64 = 0x8422_2325_cbf2_9ce4;

struct ContentRange<'a> {
    virtual_start: Option<usize>,
    physical_start: Option<usize>,
    size: usize,
    flags_mask: u64,
    required_flags: u64,
    forbidden_flags: u64,
    path: &'a SwapperPathPages,
}

struct ContentDigest {
    lo: u64,
    hi: u64,
    count: u64,
    class: u64,
    diagnostic_lo: u64,
    diagnostic_hi: u64,
    diagnostic_count: u64,
    diagnostic_chunk: u64,
    diagnostic_index: u64,
}

impl ContentDigest {
    fn new(class: u8) -> Self {
        let mut result = Self {
            lo: CONTENT_FNV_OFFSET_LO,
            hi: CONTENT_FNV_OFFSET_HI,
            count: 0,
            class: u64::from(class),
            diagnostic_lo: CONTENT_FNV_OFFSET_LO,
            diagnostic_hi: CONTENT_FNV_OFFSET_HI,
            diagnostic_count: 0,
            diagnostic_chunk: 0,
            diagnostic_index: 0,
        };
        for byte in b"LKMPTE1" {
            result.byte(*byte);
            result.diagnostic_byte(*byte);
        }
        result.byte(1);
        result.byte(class);
        result.diagnostic_byte(1);
        result.diagnostic_byte(class);
        result
    }

    fn byte(&mut self, byte: u8) {
        self.lo = (self.lo ^ u64::from(byte)).wrapping_mul(CONTENT_FNV_PRIME);
        self.hi = (self.hi ^ u64::from(byte)).wrapping_mul(CONTENT_FNV_PRIME);
    }

    fn word(&mut self, value: u64) {
        for byte in value.to_le_bytes() {
            self.byte(byte);
        }
    }

    fn diagnostic_byte(&mut self, byte: u8) {
        self.diagnostic_lo = (self.diagnostic_lo ^ u64::from(byte)).wrapping_mul(CONTENT_FNV_PRIME);
        self.diagnostic_hi = (self.diagnostic_hi ^ u64::from(byte)).wrapping_mul(CONTENT_FNV_PRIME);
    }

    fn diagnostic_word(&mut self, value: u64) {
        for byte in value.to_le_bytes() {
            self.diagnostic_byte(byte);
        }
    }

    fn reset_diagnostic_chunk(&mut self) {
        self.diagnostic_lo = CONTENT_FNV_OFFSET_LO;
        self.diagnostic_hi = CONTENT_FNV_OFFSET_HI;
        self.diagnostic_count = 0;
        for byte in b"LKMPTE1" {
            self.diagnostic_byte(*byte);
        }
        self.diagnostic_byte(1);
        self.diagnostic_byte(self.class as u8);
    }

    fn emit_diagnostic_chunk(&mut self) {
        if self.diagnostic_count == 0 {
            return;
        }
        self.diagnostic_word(self.diagnostic_count);
        swapper_content_checkpoints::content_chunk(
            self.class,
            self.diagnostic_chunk,
            self.diagnostic_count,
            self.diagnostic_lo,
            self.diagnostic_hi,
        );
        self.diagnostic_chunk = self.diagnostic_chunk.wrapping_add(1);
        self.reset_diagnostic_chunk();
    }

    fn push(&mut self, va: u64, pa: u64, flags: u64) {
        self.word(va);
        self.word(pa);
        self.word(flags);
        self.count = self.count.wrapping_add(1);
        if PT_DIAG_CLASS.load(Ordering::Relaxed) == self.class {
            match PT_DIAG_STAGE.load(Ordering::Acquire) {
                1 => {
                    self.diagnostic_word(va);
                    self.diagnostic_word(pa);
                    self.diagnostic_word(flags);
                    self.diagnostic_count += 1;
                    if self.diagnostic_count == 512 {
                        self.emit_diagnostic_chunk();
                    }
                }
                2 if self.diagnostic_index / 512 == PT_DIAG_CHUNK.load(Ordering::Relaxed) => {
                    swapper_content_checkpoints::content_item(
                        self.class,
                        self.diagnostic_index,
                        va,
                        pa,
                        flags,
                    );
                }
                _ => {}
            }
        }
        self.diagnostic_index = self.diagnostic_index.wrapping_add(1);
    }

    fn finish(mut self) -> (u64, u64) {
        if PT_DIAG_CLASS.load(Ordering::Relaxed) == self.class
            && PT_DIAG_STAGE.load(Ordering::Acquire) == 1
        {
            self.emit_diagnostic_chunk();
        }
        self.word(self.count);
        (self.lo, self.hi)
    }
}

fn valid_leaf_flags(flags: u64) -> bool {
    flags & PTE_VALID != 0
        && flags & (PTE_READ | PTE_WRITE | PTE_EXEC) != 0
        && flags & !(PTE_VALID | PTE_PERMISSION_MASK) == 0
        && !(flags & PTE_WRITE != 0 && flags & PTE_READ == 0)
}

fn lookup_path(
    root: &PageTablePage,
    path: &SwapperPathPages,
    mode: PagingMode,
    va: usize,
) -> Option<(Pte, usize)> {
    if !is_canonical(va, mode) {
        return None;
    }
    // Every intermediate page is owned by this class-specific path.  Validate
    // the actual parent PTE while following those typed pages, avoiding any
    // conversion of an arbitrary backing PA into a raw pointer.  Parent PAs
    // affect validity only and never enter the semantic digest.
    match mode {
        PagingMode::Sv57 => {
            if !swapper_table_entry_targets(
                root,
                page_table_index(va, PGD_SHIFT_SV57),
                &path.level4,
            ) || !swapper_table_entry_targets(
                &path.level4,
                page_table_index(va, P4D_SHIFT),
                &path.level3,
            ) {
                return None;
            }
        }
        PagingMode::Sv48 => {
            if !swapper_table_entry_targets(root, page_table_index(va, P4D_SHIFT), &path.level3) {
                return None;
            }
        }
        PagingMode::Sv39 => {}
    }

    let pud_page = if mode == PagingMode::Sv39 {
        root
    } else {
        &path.level3
    };
    let pud_index = page_table_index(va, PUD_SHIFT);
    let pud_entry = pud_page.read(pud_index)?;
    if valid_leaf_flags(pud_entry.flags()) {
        return Some((pud_entry, PUD_SIZE));
    }
    if !swapper_table_entry_targets(pud_page, pud_index, &path.level2) {
        return None;
    }

    let pmd_index = page_table_index(va, PMD_SHIFT);
    let pmd_entry = path.level2.read(pmd_index)?;
    if valid_leaf_flags(pmd_entry.flags()) {
        return Some((pmd_entry, PMD_SIZE));
    }
    if !swapper_table_entry_targets(&path.level2, pmd_index, &path.level1) {
        return None;
    }

    let entry = path.level1.read(page_table_index(va, PAGE_SHIFT))?;
    valid_leaf_flags(entry.flags()).then_some((entry, PAGE_SIZE))
}

fn swapper_table_entry_targets(page: &PageTablePage, index: usize, target: &PageTablePage) -> bool {
    let Some(entry) = page.read(index) else {
        return false;
    };
    let Ok(target_pa) = runtime_physical_address(target.physical_address()) else {
        return false;
    };
    entry.flags() == PAGE_TABLE_FLAGS && entry.physical_address() == target_pa as u64
}

// SAFETY: the root is the first page of this page-aligned object, so the
// exported address is exactly the SATP root page. Intermediate pages are
// uniquely owned by the object and written only on the boot hart.
#[unsafe(export_name = "swapper_pg_dir")]
pub(crate) static SWAPPER_PAGE_TABLE: SwapperPageTableType = SwapperPageTableType::new();

/// Public Rust boundary for the M1 transition.  The zero-sized handle keeps
/// the lifecycle API separate from the linker-visible `swapper_pg_dir`
/// storage while ensuring callers can only enable it with an Online MemBlock.
pub(crate) struct SwapperPageTable;

impl SwapperPageTable {
    pub(crate) fn enable(memblock: &mut MemBlock) -> VmResult<()> {
        setup_vm_final_inner(memblock)
    }
}

// SAFETY: this is the sole linker-visible definition of `trampoline_pg_dir`.
// `PageTableType` has a C representation and `root` is its first field, so the
// exported object's address is exactly the page-aligned trampoline root address.
// The remaining fields are the root's uniquely owned intermediate page-table
// pages; no alias or copy of this object exists.
#[unsafe(export_name = "trampoline_pg_dir")]
pub(crate) static TRAMPOLINE_PAGE_TABLE: PageTableType<NoFixmap> = PageTableType::new_trampoline();

fn page_table_index(virtual_address: usize, shift: usize) -> usize {
    (virtual_address >> shift) & PAGE_TABLE_INDEX_MASK
}

fn is_canonical(virtual_address: usize, mode: PagingMode) -> bool {
    let address_bits = mode.virtual_address_bits();
    let sign_bit = 1_usize << (address_bits - 1);
    let upper_mask = usize::MAX << address_bits;
    if virtual_address & sign_bit == 0 {
        virtual_address & upper_mask == 0
    } else {
        virtual_address & upper_mask == upper_mask
    }
}

fn ranges_share_level2_page(start: usize, end: usize) -> bool {
    start >> PUD_SHIFT == end >> PUD_SHIFT
}

fn map_2m(
    root: &PageTablePage,
    path: &PathPages,
    mode: PagingMode,
    virtual_address: usize,
    physical_address: usize,
    flags: u64,
) -> VmResult<()> {
    if virtual_address & (PMD_SIZE - 1) != 0 || physical_address & (PMD_SIZE - 1) != 0 {
        return Err(VmSetupError::InvalidAlignment);
    }
    if !is_canonical(virtual_address, mode) {
        return Err(VmSetupError::InvalidMappingRange);
    }

    match mode {
        PagingMode::Sv57 => {
            root.install(
                page_table_index(virtual_address, mode.top_level_shift()),
                Pte::table(path.level4.physical_address())?,
            )?;
            path.level4.install(
                page_table_index(virtual_address, P4D_SHIFT),
                Pte::table(path.level3.physical_address())?,
            )?;
            path.level3.install(
                page_table_index(virtual_address, PUD_SHIFT),
                Pte::table(path.level2.physical_address())?,
            )?;
        }
        PagingMode::Sv48 => {
            root.install(
                page_table_index(virtual_address, mode.top_level_shift()),
                Pte::table(path.level3.physical_address())?,
            )?;
            path.level3.install(
                page_table_index(virtual_address, PUD_SHIFT),
                Pte::table(path.level2.physical_address())?,
            )?;
        }
        PagingMode::Sv39 => {
            root.install(
                page_table_index(virtual_address, mode.top_level_shift()),
                Pte::table(path.level2.physical_address())?,
            )?;
        }
    }

    path.level2.install(
        page_table_index(virtual_address, PMD_SHIFT),
        Pte::leaf_2m(physical_address, flags)?,
    )
}

fn map_swapper_1g(
    mode: PagingMode,
    virtual_address: usize,
    physical_address: usize,
    flags: u64,
    path: &SwapperPathPages,
) -> VmResult<()> {
    if virtual_address & (PUD_SIZE - 1) != 0 || physical_address & (PUD_SIZE - 1) != 0 {
        return Err(VmSetupError::InvalidAlignment);
    }
    if !is_canonical(virtual_address, mode) {
        return Err(VmSetupError::InvalidMappingRange);
    }
    let root = &SWAPPER_PAGE_TABLE.root;
    let l4_pa = runtime_physical_address(path.level4.physical_address())?;
    let l3_pa = runtime_physical_address(path.level3.physical_address())?;
    match mode {
        PagingMode::Sv57 => {
            root.install(
                page_table_index(virtual_address, PGD_SHIFT_SV57),
                Pte::table(l4_pa)?,
            )?;
            path.level4.install(
                page_table_index(virtual_address, P4D_SHIFT),
                Pte::table(l3_pa)?,
            )?;
            path.level3.install(
                page_table_index(virtual_address, PUD_SHIFT),
                Pte::leaf_1g(physical_address, flags)?,
            )?;
        }
        PagingMode::Sv48 => {
            root.install(
                page_table_index(virtual_address, P4D_SHIFT),
                Pte::table(l3_pa)?,
            )?;
            path.level3.install(
                page_table_index(virtual_address, PUD_SHIFT),
                Pte::leaf_1g(physical_address, flags)?,
            )?;
        }
        PagingMode::Sv39 => {
            root.install(
                page_table_index(virtual_address, PUD_SHIFT),
                Pte::leaf_1g(physical_address, flags)?,
            )?;
        }
    }
    Ok(())
}

fn map_swapper_2m(
    mode: PagingMode,
    virtual_address: usize,
    physical_address: usize,
    flags: u64,
    path: &SwapperPathPages,
) -> VmResult<()> {
    if virtual_address & (PMD_SIZE - 1) != 0 || physical_address & (PMD_SIZE - 1) != 0 {
        return Err(VmSetupError::InvalidAlignment);
    }
    if !is_canonical(virtual_address, mode) {
        return Err(VmSetupError::InvalidMappingRange);
    }
    let root = &SWAPPER_PAGE_TABLE.root;
    let l4_pa = runtime_physical_address(path.level4.physical_address())?;
    let l3_pa = runtime_physical_address(path.level3.physical_address())?;
    let l2_pa = runtime_physical_address(path.level2.physical_address())?;
    match mode {
        PagingMode::Sv57 => {
            root.install(
                page_table_index(virtual_address, PGD_SHIFT_SV57),
                Pte::table(l4_pa)?,
            )?;
            path.level4.install(
                page_table_index(virtual_address, P4D_SHIFT),
                Pte::table(l3_pa)?,
            )?;
            path.level3.install(
                page_table_index(virtual_address, PUD_SHIFT),
                Pte::table(l2_pa)?,
            )?;
        }
        PagingMode::Sv48 => {
            root.install(
                page_table_index(virtual_address, P4D_SHIFT),
                Pte::table(l3_pa)?,
            )?;
            path.level3.install(
                page_table_index(virtual_address, PUD_SHIFT),
                Pte::table(l2_pa)?,
            )?;
        }
        PagingMode::Sv39 => {
            root.install(
                page_table_index(virtual_address, PUD_SHIFT),
                Pte::table(l2_pa)?,
            )?;
        }
    }
    path.level2.install(
        page_table_index(virtual_address, PMD_SHIFT),
        Pte::leaf_2m(physical_address, flags)?,
    )
}

fn map_swapper_4k(
    mode: PagingMode,
    virtual_address: usize,
    physical_address: usize,
    flags: u64,
    path: &SwapperPathPages,
) -> VmResult<()> {
    if virtual_address & (PAGE_SIZE - 1) != 0 || physical_address & (PAGE_SIZE - 1) != 0 {
        return Err(VmSetupError::InvalidAlignment);
    }
    if !is_canonical(virtual_address, mode) {
        return Err(VmSetupError::InvalidMappingRange);
    }
    let root = &SWAPPER_PAGE_TABLE.root;
    let l4_pa = runtime_physical_address(path.level4.physical_address())?;
    let l3_pa = runtime_physical_address(path.level3.physical_address())?;
    let l2_pa = runtime_physical_address(path.level2.physical_address())?;
    let l1_pa = runtime_physical_address(path.level1.physical_address())?;
    match mode {
        PagingMode::Sv57 => {
            root.install(
                page_table_index(virtual_address, PGD_SHIFT_SV57),
                Pte::table(l4_pa)?,
            )?;
            path.level4.install(
                page_table_index(virtual_address, P4D_SHIFT),
                Pte::table(l3_pa)?,
            )?;
            path.level3.install(
                page_table_index(virtual_address, PUD_SHIFT),
                Pte::table(l2_pa)?,
            )?;
        }
        PagingMode::Sv48 => {
            root.install(
                page_table_index(virtual_address, P4D_SHIFT),
                Pte::table(l3_pa)?,
            )?;
            path.level3.install(
                page_table_index(virtual_address, PUD_SHIFT),
                Pte::table(l2_pa)?,
            )?;
        }
        PagingMode::Sv39 => {
            root.install(
                page_table_index(virtual_address, PUD_SHIFT),
                Pte::table(l2_pa)?,
            )?;
        }
    }
    path.level2.install(
        page_table_index(virtual_address, PMD_SHIFT),
        Pte::table(l1_pa)?,
    )?;
    path.level1.install(
        page_table_index(virtual_address, PAGE_SHIFT),
        Pte::leaf_4k(physical_address, flags)?,
    )
}

fn table_entry_targets(page: &PageTablePage, index: usize, target: &PageTablePage) -> bool {
    let Some(entry) = page.read(index) else {
        return false;
    };
    entry.flags() == PAGE_TABLE_FLAGS
        && entry.physical_address() == target.physical_address() as u64
}

fn observe_leaf_2m(
    root: &PageTablePage,
    path: &PathPages,
    mode: PagingMode,
    virtual_address: usize,
) -> (Option<Pte>, bool) {
    let path_ok = match mode {
        PagingMode::Sv57 => {
            table_entry_targets(
                root,
                page_table_index(virtual_address, PGD_SHIFT_SV57),
                &path.level4,
            ) && table_entry_targets(
                &path.level4,
                page_table_index(virtual_address, P4D_SHIFT),
                &path.level3,
            ) && table_entry_targets(
                &path.level3,
                page_table_index(virtual_address, PUD_SHIFT),
                &path.level2,
            )
        }
        PagingMode::Sv48 => {
            table_entry_targets(
                root,
                page_table_index(virtual_address, P4D_SHIFT),
                &path.level3,
            ) && table_entry_targets(
                &path.level3,
                page_table_index(virtual_address, PUD_SHIFT),
                &path.level2,
            )
        }
        PagingMode::Sv39 => table_entry_targets(
            root,
            page_table_index(virtual_address, PUD_SHIFT),
            &path.level2,
        ),
    };
    (
        path.level2
            .read(page_table_index(virtual_address, PMD_SHIFT)),
        path_ok,
    )
}

#[repr(C)]
struct KernelMapType {
    page_offset: AtomicU64,
    virtual_address: AtomicU64,
    physical_address: AtomicU64,
    image_size: AtomicU64,
    virtual_physical_offset: AtomicU64,
}

impl KernelMapType {
    const fn new() -> Self {
        Self {
            page_offset: AtomicU64::new(0),
            virtual_address: AtomicU64::new(0),
            physical_address: AtomicU64::new(0),
            image_size: AtomicU64::new(0),
            virtual_physical_offset: AtomicU64::new(0),
        }
    }

    fn preset(&self) -> VmResult<()> {
        let physical_address = linker_start_address();
        let physical_end = linker_end_address();
        let image_size = physical_end
            .checked_sub(physical_address)
            .ok_or(VmSetupError::AddressOverflow)?;
        if image_size == 0
            || physical_address & (PMD_SIZE - 1) != 0
            || KERNEL_LINK_ADDR & (PMD_SIZE - 1) != 0
        {
            return Err(VmSetupError::InvalidAlignment);
        }
        let virtual_end = KERNEL_LINK_ADDR
            .checked_add(image_size)
            .ok_or(VmSetupError::AddressOverflow)?;
        if virtual_end > ADDRESS_SPACE_LAST_PAGE {
            return Err(VmSetupError::AddressOverflow);
        }
        let virtual_physical_offset = KERNEL_LINK_ADDR
            .checked_sub(physical_address)
            .ok_or(VmSetupError::AddressOverflow)?;

        self.page_offset.store(0, Ordering::Relaxed);
        self.virtual_address
            .store(KERNEL_LINK_ADDR as u64, Ordering::Relaxed);
        self.physical_address
            .store(physical_address as u64, Ordering::Relaxed);
        self.image_size.store(image_size as u64, Ordering::Relaxed);
        self.virtual_physical_offset
            .store(virtual_physical_offset as u64, Ordering::Relaxed);
        Ok(())
    }

    fn setup(&self, mode: PagingMode) {
        self.page_offset
            .store(mode.page_offset() as u64, Ordering::Relaxed);
    }

    fn virtual_address(&self) -> usize {
        self.virtual_address.load(Ordering::Relaxed) as usize
    }

    fn physical_address(&self) -> usize {
        self.physical_address.load(Ordering::Relaxed) as usize
    }

    fn image_size(&self) -> usize {
        self.image_size.load(Ordering::Relaxed) as usize
    }

    fn checkpoint_observation(&self, mode: PagingMode) -> KernelMapObservation {
        KernelMapObservation {
            kernel_va: self.virtual_address.load(Ordering::Acquire),
            kernel_pa: self.physical_address.load(Ordering::Acquire),
            page_offset: self.page_offset.load(Ordering::Acquire),
            kernel_va_pa_offset: self.virtual_physical_offset.load(Ordering::Acquire),
            mode: mode as u64,
            levels: match mode {
                PagingMode::Sv57 => 5,
                PagingMode::Sv48 => 4,
                PagingMode::Sv39 => 3,
            },
            top_shift: mode.top_level_shift() as u64,
        }
    }
}

#[repr(C)]
struct VmType {
    kernel_map: KernelMapType,
    early_dtb_pa: AtomicU64,
    early_dtb_va: AtomicU64,
    setup_error: AtomicU32,
    early: PageTableType<FixmapPages>,
}

impl VmType {
    const fn new() -> Self {
        Self {
            kernel_map: KernelMapType::new(),
            early_dtb_pa: AtomicU64::new(0),
            early_dtb_va: AtomicU64::new(0),
            setup_error: AtomicU32::new(0),
            early: PageTableType::new_early(),
        }
    }

    fn preset(&self, _dtb_pa: usize) -> VmResult<()> {
        SATP_MODE.store(0, Ordering::Relaxed);
        self.setup_error.store(0, Ordering::Relaxed);
        self.early_dtb_pa.store(0, Ordering::Relaxed);
        self.early_dtb_va.store(0, Ordering::Relaxed);

        self.kernel_map.preset()?;
        TRAMPOLINE_PAGE_TABLE.preset();
        self.early.preset();

        let mode = self.probe_paging_mode()?;
        self.kernel_map.setup(mode);
        SATP_MODE.store(mode.satp_bits(), Ordering::Release);
        let satp = read_satp();
        if satp != 0 {
            return Err(VmSetupError::InvalidMappingRange);
        }
        let kernel = self.kernel_map.checkpoint_observation(mode);
        checkpoints::kernel_map_ready(kernel, satp);
        checkpoints::preset_complete(kernel, satp);
        Ok(())
    }

    fn setup(&self, dtb_pa: usize) -> VmResult<()> {
        let mode = selected_paging_mode()?;
        TRAMPOLINE_PAGE_TABLE.preset();
        self.early.preset();

        self.setup_trampoline(mode)?;
        checkpoints::trampoline_ready(self.observe_trampoline(mode));
        self.setup_early_kernel(mode)?;
        checkpoints::early_kernel_ready(self.observe_early_kernel(mode)?);
        self.setup_early_dtb(mode, dtb_pa)?;
        checkpoints::early_dtb_ready(self.observe_early_dtb(mode, dtb_pa));
        page_table_write_fence();

        let satp = read_satp();
        if satp != 0 {
            return Err(VmSetupError::InvalidMappingRange);
        }
        checkpoints::setup_complete(
            self.kernel_map.checkpoint_observation(mode),
            self.observe_trampoline(mode),
            self.observe_early_kernel(mode)?,
            self.observe_early_dtb(mode, dtb_pa),
            satp,
        );
        Ok(())
    }

    fn early_root_address(&self) -> VmResult<usize> {
        let root_ppn = self.early.root_satp_ppn()?;
        Ok((root_ppn << PAGE_SHIFT) as usize)
    }

    fn early_dtb_mapping(&self) -> Option<EarlyDtbMapping> {
        let virtual_address = self.early_dtb_va.load(Ordering::Acquire) as usize;
        let physical_address = self.early_dtb_pa.load(Ordering::Acquire) as usize;
        if virtual_address == 0 || physical_address == 0 {
            return None;
        }
        let offset = physical_address & (PMD_SIZE - 1);
        let len = (2 * PMD_SIZE).checked_sub(offset)?;
        Some(EarlyDtbMapping::new(physical_address, virtual_address, len))
    }

    fn probe_paging_mode(&self) -> VmResult<PagingMode> {
        for mode in [PagingMode::Sv57, PagingMode::Sv48, PagingMode::Sv39] {
            if probe_candidate(&self.early, mode)? {
                return Ok(mode);
            }
        }
        Err(VmSetupError::UnsupportedPagingMode)
    }

    fn setup_trampoline(&self, mode: PagingMode) -> VmResult<()> {
        TRAMPOLINE_PAGE_TABLE.map_kernel_2m(
            mode,
            self.kernel_map.virtual_address(),
            self.kernel_map.physical_address(),
            PAGE_KERNEL_EXEC_FLAGS,
        )
    }

    fn setup_early_kernel(&self, mode: PagingMode) -> VmResult<()> {
        let virtual_start = self.kernel_map.virtual_address();
        let physical_start = self.kernel_map.physical_address();
        let image_size = self.kernel_map.image_size();
        let rounded_size = image_size
            .checked_add(PMD_SIZE - 1)
            .ok_or(VmSetupError::AddressOverflow)?
            & PMD_MASK;
        let virtual_last = virtual_start
            .checked_add(rounded_size - PMD_SIZE)
            .ok_or(VmSetupError::AddressOverflow)?;
        if !ranges_share_level2_page(virtual_start, virtual_last) {
            return Err(VmSetupError::PageTableCapacityExceeded);
        }

        let mut offset = 0;
        while offset < rounded_size {
            let virtual_address = virtual_start
                .checked_add(offset)
                .ok_or(VmSetupError::AddressOverflow)?;
            let physical_address = physical_start
                .checked_add(offset)
                .ok_or(VmSetupError::AddressOverflow)?;
            self.early.map_kernel_2m(
                mode,
                virtual_address,
                physical_address,
                PAGE_KERNEL_EXEC_FLAGS,
            )?;
            offset = offset
                .checked_add(PMD_SIZE)
                .ok_or(VmSetupError::AddressOverflow)?;
        }
        Ok(())
    }

    fn setup_early_dtb(&self, mode: PagingMode, dtb_pa: usize) -> VmResult<()> {
        let physical_start = dtb_pa & PMD_MASK;
        let physical_second = physical_start
            .checked_add(PMD_SIZE)
            .ok_or(VmSetupError::AddressOverflow)?;
        physical_second
            .checked_add(PMD_SIZE)
            .ok_or(VmSetupError::AddressOverflow)?;

        let virtual_start = mode.fix_fdt_va();
        let virtual_second = virtual_start
            .checked_add(PMD_SIZE)
            .ok_or(VmSetupError::AddressOverflow)?;
        if !ranges_share_level2_page(virtual_start, virtual_second) {
            return Err(VmSetupError::PageTableCapacityExceeded);
        }

        self.early
            .map_fixmap_2m(mode, virtual_start, physical_start, PAGE_KERNEL_FLAGS)?;
        self.early
            .map_fixmap_2m(mode, virtual_second, physical_second, PAGE_KERNEL_FLAGS)?;

        let virtual_offset = dtb_pa & (PMD_SIZE - 1);
        let early_dtb_va = virtual_start
            .checked_add(virtual_offset)
            .ok_or(VmSetupError::AddressOverflow)?;
        self.early_dtb_pa.store(dtb_pa as u64, Ordering::Relaxed);
        self.early_dtb_va
            .store(early_dtb_va as u64, Ordering::Relaxed);
        Ok(())
    }

    fn observe_trampoline(&self, mode: PagingMode) -> TrampolineObservation {
        let virtual_address = self.kernel_map.virtual_address();
        let (leaf, path_ok) = TRAMPOLINE_PAGE_TABLE.observe_kernel_leaf(mode, virtual_address);
        TrampolineObservation {
            mode: mode as u64,
            va: virtual_address as u64,
            pa: leaf.map_or(0, Pte::physical_address),
            size: PMD_SIZE as u64,
            flags: leaf.map_or(0, Pte::flags),
            path_ok: u64::from(path_ok),
        }
    }

    fn observe_early_kernel(&self, mode: PagingMode) -> VmResult<EarlyKernelObservation> {
        let virtual_start = self.kernel_map.virtual_address();
        let physical_start = self.kernel_map.physical_address();
        let image_size = self.kernel_map.image_size();
        let rounded_size = image_size
            .checked_add(PMD_SIZE - 1)
            .ok_or(VmSetupError::AddressOverflow)?
            & PMD_MASK;
        let (first_leaf, first_path_ok) = self.early.observe_kernel_leaf(mode, virtual_start);
        let mut coverage_ok = first_path_ok;
        let mut offset = 0_usize;
        while offset < rounded_size {
            let virtual_address = virtual_start
                .checked_add(offset)
                .ok_or(VmSetupError::AddressOverflow)?;
            let expected_pa = physical_start
                .checked_add(offset)
                .ok_or(VmSetupError::AddressOverflow)? as u64;
            let (leaf, path_ok) = self.early.observe_kernel_leaf(mode, virtual_address);
            coverage_ok &= path_ok
                && leaf.is_some_and(|entry| {
                    entry.physical_address() == expected_pa
                        && entry.flags() == PAGE_KERNEL_EXEC_FLAGS
                });
            offset = offset
                .checked_add(PMD_SIZE)
                .ok_or(VmSetupError::AddressOverflow)?;
        }
        Ok(EarlyKernelObservation {
            mode: mode as u64,
            va: virtual_start as u64,
            pa: first_leaf.map_or(0, Pte::physical_address),
            flags: first_leaf.map_or(0, Pte::flags),
            coverage_ok: u64::from(coverage_ok),
        })
    }

    fn observe_early_dtb(&self, mode: PagingMode, dtb_pa: usize) -> EarlyDtbObservation {
        let fix_va = mode.fix_fdt_va();
        let second_va = fix_va + PMD_SIZE;
        let (leaf0, path0_ok) =
            observe_leaf_2m(&self.early.root, &self.early.fixmap.path, mode, fix_va);
        let (leaf1, path1_ok) =
            observe_leaf_2m(&self.early.root, &self.early.fixmap.path, mode, second_va);
        let expected_pa = (dtb_pa & PMD_MASK) as u64;
        let coverage_ok = path0_ok
            && path1_ok
            && leaf0.is_some_and(|entry| {
                entry.physical_address() == expected_pa && entry.flags() == PAGE_KERNEL_FLAGS
            })
            && leaf1.is_some_and(|entry| {
                entry.physical_address() == expected_pa + PMD_SIZE as u64
                    && entry.flags() == PAGE_KERNEL_FLAGS
            });
        EarlyDtbObservation {
            mode: mode as u64,
            dtb_pa: self.early_dtb_pa.load(Ordering::Acquire),
            dtb_va: self.early_dtb_va.load(Ordering::Acquire),
            fix_va: fix_va as u64,
            leaf0_pa: leaf0.map_or(0, Pte::physical_address),
            leaf0_flags: leaf0.map_or(0, Pte::flags),
            leaf1_pa: leaf1.map_or(0, Pte::physical_address),
            leaf1_flags: leaf1.map_or(0, Pte::flags),
            size: (2 * PMD_SIZE) as u64,
            coverage_ok: u64::from(coverage_ok),
        }
    }

    fn fail_stop(&self, error: VmSetupError) -> ! {
        self.setup_error.store(error as u32, Ordering::Release);
        restore_bare_satp();
        loop {
            // SAFETY: setup has failed on the boot hart with interrupts masked.
            // WFI is used only as a fail-stop hint; the local trap vector also
            // targets a permanent park loop if the instruction traps.
            unsafe { asm!("wfi", options(nomem, nostack)) };
        }
    }
}

#[inline(never)]
fn probe_candidate(page_table: &PageTableType<FixmapPages>, mode: PagingMode) -> VmResult<bool> {
    page_table.clear_all();

    let probe_address = probe_candidate as *const () as usize;
    let first_pmd = probe_address & PMD_MASK;
    let second_pmd = first_pmd
        .checked_add(PMD_SIZE)
        .ok_or(VmSetupError::AddressOverflow)?;
    if !ranges_share_level2_page(first_pmd, second_pmd) {
        return Err(VmSetupError::PageTableCapacityExceeded);
    }

    let mapping_result = (|| {
        page_table.map_kernel_2m(mode, first_pmd, first_pmd, PAGE_KERNEL_EXEC_FLAGS)?;
        page_table.map_kernel_2m(mode, second_pmd, second_pmd, PAGE_KERNEL_EXEC_FLAGS)?;
        Ok(())
    })();
    if let Err(error) = mapping_result {
        page_table.clear_all();
        return Err(error);
    }

    let root_ppn = match page_table.root_satp_ppn() {
        Ok(root_ppn) => root_ppn,
        Err(error) => {
            page_table.clear_all();
            return Err(error);
        }
    };
    let candidate = mode.satp_bits() | root_ppn;
    let observed = probe_satp(candidate);
    page_table.clear_all();
    page_table_write_fence();
    if read_satp() != 0 {
        return Err(VmSetupError::InvalidMappingRange);
    }
    Ok(observed == candidate)
}

#[inline(always)]
fn probe_satp(candidate: u64) -> u64 {
    let observed;
    // SAFETY: both PMDs containing this inline instruction sequence have an
    // executable identity mapping in `candidate`. The sequence performs no
    // stack or memory access while SATP is nonzero, swaps back to Bare before
    // leaving the asm block, and globally fences translations on both sides.
    unsafe {
        asm!(
            "fence rw, rw",
            "sfence.vma zero, zero",
            "csrw satp, {candidate}",
            "csrrw {observed}, satp, zero",
            "sfence.vma zero, zero",
            candidate = in(reg) candidate,
            observed = lateout(reg) observed,
            options(nostack),
        );
    }
    observed
}

#[inline(always)]
fn read_satp() -> u64 {
    let value;
    // SAFETY: reading the supervisor SATP CSR has no memory aliasing effect;
    // setup_vm executes in S-mode on the boot hart.
    unsafe { asm!("csrr {value}, satp", value = out(reg) value, options(nomem, nostack)) };
    value
}

#[inline(always)]
fn restore_bare_satp() {
    // SAFETY: the boot hart is executing with MMU-off-capable PC-relative code.
    // Writing zero selects Bare, and the following global fence discards any
    // translation state left by an interrupted probe.
    unsafe {
        asm!("csrw satp, zero", "sfence.vma zero, zero", options(nostack),);
    }
}

#[inline(always)]
fn page_table_write_fence() {
    // SAFETY: this is the required hardware publication boundary for PTE
    // stores made by the boot hart; it does not dereference memory itself.
    unsafe { asm!("fence rw, rw", options(nostack)) };
}

// SAFETY: the linker script uniquely defines both byte-sized symbols at image
// boundaries; this block only declares their addresses and never reads a byte.
unsafe extern "C" {
    static _start: u8;
    static _end: u8;
    static _text_start: u8;
    static _text_end: u8;
    static _rodata_start: u8;
    static _rodata_end: u8;
}

#[inline(always)]
fn linker_start_address() -> usize {
    // `_start` is defined by the kernel linker script. Taking its address does
    // not dereference it, and medany materializes the MMU-off runtime physical
    // address through a PC-relative relocation.
    core::ptr::addr_of!(_start) as usize
}

#[inline(always)]
fn linker_end_address() -> usize {
    // `_end` is defined by the kernel linker script. Taking its address does not
    // dereference it and uses the same PC-relative MMU-off convention as
    // `_start`.
    core::ptr::addr_of!(_end) as usize
}

#[inline(always)]
fn linker_text_start_address() -> usize {
    core::ptr::addr_of!(_text_start) as usize
}

#[inline(always)]
fn linker_text_end_address() -> usize {
    core::ptr::addr_of!(_text_end) as usize
}

#[inline(always)]
fn linker_rodata_start_address() -> usize {
    core::ptr::addr_of!(_rodata_start) as usize
}

#[inline(always)]
fn linker_rodata_end_address() -> usize {
    core::ptr::addr_of!(_rodata_end) as usize
}

static VM: VmType = VmType::new();

static PT_DIAG_CLASS: AtomicU64 = AtomicU64::new(0);
static PT_DIAG_STAGE: AtomicU64 = AtomicU64::new(0);
static PT_DIAG_CHUNK: AtomicU64 = AtomicU64::new(0);

pub(crate) fn configure_page_table_diagnostics(command_line: &str) {
    if !swapper_content_checkpoints::ENABLED {
        return;
    }
    PT_DIAG_CLASS.store(0, Ordering::Relaxed);
    PT_DIAG_STAGE.store(0, Ordering::Relaxed);
    PT_DIAG_CHUNK.store(0, Ordering::Relaxed);
    let Some(value) = command_line
        .split_ascii_whitespace()
        .find_map(|token| token.strip_prefix("lkm2.ptdiag="))
    else {
        return;
    };
    let mut parts = value.split(',');
    let class = match parts.next() {
        Some("fixmap") => 1,
        Some("linear") => 2,
        Some("kernel") => 3,
        _ => return,
    };
    let stage = match parts.next() {
        Some("chunks") => 1,
        Some("items") => 2,
        _ => return,
    };
    let mut chunk = 0_u64;
    if stage == 2 {
        let Some(value) = parts.next() else {
            return;
        };
        for byte in value.bytes() {
            if !byte.is_ascii_digit() {
                return;
            }
            chunk = match chunk
                .checked_mul(10)
                .and_then(|current| current.checked_add(u64::from(byte - b'0')))
            {
                Some(value) => value,
                None => return,
            };
        }
    }
    if parts.next().is_some() {
        return;
    }
    PT_DIAG_CHUNK.store(chunk, Ordering::Relaxed);
    PT_DIAG_CLASS.store(class, Ordering::Relaxed);
    PT_DIAG_STAGE.store(stage, Ordering::Release);
}

fn runtime_physical_address(virtual_address: usize) -> VmResult<usize> {
    let kernel_virtual = VM.kernel_map.virtual_address();
    let kernel_physical = VM.kernel_map.physical_address();
    virtual_address
        .checked_sub(kernel_virtual)
        .and_then(|offset| kernel_physical.checked_add(offset))
        .ok_or(VmSetupError::AddressOverflow)
}

fn memblock_error(_error: MemBlockError) -> VmSetupError {
    VmSetupError::PageTableCapacityExceeded
}

/// Return the first page that belongs in the direct map.  Platform firmware
/// may reserve a contiguous prefix of the first Memory range as NOMAP (OpenSBI
/// does so on QEMU virt).  Linux omits that prefix from for_each_mem_range();
/// derive the equivalent boundary from the normalized Rust reservation set.
fn linear_mappable_physical_start(memblock: &MemBlock) -> Option<usize> {
    let (memory_base, _memory_end) = memblock.memory_ranges().next()?;
    let mut cursor = usize::try_from(memory_base).ok()?;
    for (base, end) in memblock.reserved_ranges() {
        let base = usize::try_from(base).ok()?;
        let end = usize::try_from(end).ok()?;
        if end <= cursor {
            continue;
        }
        if base > cursor {
            break;
        }
        cursor = end;
    }
    cursor
        .checked_add(PAGE_SIZE - 1)
        .map(|end| end & !(PAGE_SIZE - 1))
}

fn map_swapper_region(
    mode: PagingMode,
    virtual_start: usize,
    physical_start: usize,
    size: usize,
    flags: u64,
    path: &SwapperPathPages,
) -> VmResult<()> {
    let mut virtual_address = virtual_start;
    let mut physical_address = physical_start;
    let end = virtual_start
        .checked_add(size)
        .ok_or(VmSetupError::AddressOverflow)?;
    while virtual_address < end {
        let remaining = end - virtual_address;
        if virtual_address & (PUD_SIZE - 1) == 0
            && physical_address & (PUD_SIZE - 1) == 0
            && remaining >= PUD_SIZE
        {
            map_swapper_1g(mode, virtual_address, physical_address, flags, path)?;
            virtual_address += PUD_SIZE;
            physical_address = physical_address
                .checked_add(PUD_SIZE)
                .ok_or(VmSetupError::AddressOverflow)?;
        } else if virtual_address & (PMD_SIZE - 1) == 0
            && physical_address & (PMD_SIZE - 1) == 0
            && remaining >= PMD_SIZE
        {
            map_swapper_2m(mode, virtual_address, physical_address, flags, path)?;
            virtual_address += PMD_SIZE;
            physical_address = physical_address
                .checked_add(PMD_SIZE)
                .ok_or(VmSetupError::AddressOverflow)?;
        } else {
            map_swapper_4k(mode, virtual_address, physical_address, flags, path)?;
            virtual_address += PAGE_SIZE;
            physical_address = physical_address
                .checked_add(PAGE_SIZE)
                .ok_or(VmSetupError::AddressOverflow)?;
        }
    }
    Ok(())
}

fn map_swapper_linear_memory(
    mode: PagingMode,
    linear_physical_base: usize,
    physical_start: usize,
    physical_end: usize,
) -> VmResult<()> {
    let offset = physical_start
        .checked_sub(linear_physical_base)
        .ok_or(VmSetupError::AddressOverflow)?;
    let virtual_start = mode
        .page_offset()
        .checked_add(offset)
        .ok_or(VmSetupError::AddressOverflow)?;
    map_swapper_region(
        mode,
        virtual_start,
        physical_start,
        physical_end
            .checked_sub(physical_start)
            .ok_or(VmSetupError::AddressOverflow)?,
        PAGE_KERNEL_FLAGS,
        &SWAPPER_PAGE_TABLE.linear_path,
    )
}

fn map_swapper_kernel(mode: PagingMode) -> VmResult<()> {
    let virtual_start = VM.kernel_map.virtual_address();
    let physical_start = VM.kernel_map.physical_address();
    let image_size = VM.kernel_map.image_size();
    let end = virtual_start
        .checked_add(image_size)
        .ok_or(VmSetupError::AddressOverflow)?;
    let text_start = linker_text_start_address();
    let text_end = linker_text_end_address();
    let rodata_start = linker_rodata_start_address();
    let rodata_end = linker_rodata_end_address();
    let flags_for = |address: usize| {
        if address >= text_start && address < text_end {
            PTE_VALID | PTE_READ | PTE_EXEC | PTE_GLOBAL | PTE_ACCESSED | PTE_DIRTY
        } else if address >= rodata_start && address < rodata_end {
            PTE_VALID | PTE_READ | PTE_GLOBAL | PTE_ACCESSED
        } else {
            PAGE_KERNEL_FLAGS
        }
    };

    let mut virtual_address = virtual_start & !(PMD_SIZE - 1);
    while virtual_address < end {
        let physical_address = physical_start
            .checked_add(virtual_address - virtual_start)
            .ok_or(VmSetupError::AddressOverflow)?;
        let next = virtual_address
            .checked_add(PMD_SIZE)
            .ok_or(VmSetupError::AddressOverflow)?;
        let section = flags_for(virtual_address);
        let same_section = flags_for(next.saturating_sub(1)) == section;
        if virtual_address >= virtual_start
            && next <= end
            && physical_address & (PMD_SIZE - 1) == 0
            && same_section
        {
            map_swapper_2m(
                mode,
                virtual_address,
                physical_address,
                section,
                &SWAPPER_PAGE_TABLE.kernel_path,
            )?;
            virtual_address = next;
        } else {
            let page_end = core::cmp::min(next, end);
            let mut page = core::cmp::max(virtual_address, virtual_start) & !(PAGE_SIZE - 1);
            while page < page_end {
                let pa = physical_start
                    .checked_add(page - virtual_start)
                    .ok_or(VmSetupError::AddressOverflow)?;
                map_swapper_4k(
                    mode,
                    page,
                    pa,
                    flags_for(page),
                    &SWAPPER_PAGE_TABLE.kernel_path,
                )?;
                page += PAGE_SIZE;
            }
            virtual_address = next;
        }
    }
    Ok(())
}

fn pt_ops_set_late() {
    // The generic buddy-backed callbacks are intentionally deferred.  This
    // bit records only that the late allocation mode has been selected.
    SWAPPER_PAGE_TABLE
        .late_mode_selected
        .store(1, Ordering::Release);
}

fn setup_vm_final_inner(memblock: &mut MemBlock) -> VmResult<()> {
    let mode = selected_paging_mode()?;
    SWAPPER_PAGE_TABLE.clear();

    // Reserve the deterministic page-table slots from MemBlock.  The static
    // pages below are the executable backing for this no-heap image; keeping
    // the reservations in MemBlock preserves the allocator contract and makes
    // later dynamic consumers observe the pages as unavailable.
    for _ in 0..12 {
        memblock
            .allocate_page_table_page(PAGE_SIZE as u64, PAGE_SIZE as u64)
            .map_err(memblock_error)?;
    }

    let linear_physical_base = memblock
        .memory_ranges()
        .next()
        .and_then(|(base, _limit)| usize::try_from(base).ok())
        .map(|base| base & PMD_MASK)
        .ok_or(VmSetupError::InvalidMappingRange)?;
    let linear_mappable_start =
        linear_mappable_physical_start(memblock).ok_or(VmSetupError::InvalidMappingRange)?;
    let mut first_memory = None;
    for (base, end) in memblock.memory_ranges() {
        let memory_start = usize::try_from(base).map_err(|_| VmSetupError::AddressOverflow)?;
        let physical_end = usize::try_from(end).map_err(|_| VmSetupError::AddressOverflow)?;
        if first_memory.is_none() {
            // PAGE_OFFSET is the fixed linear-map base; the physical memory
            // start is carried separately as the representative PA.
            first_memory = Some((mode.page_offset(), base));
        }
        let physical_start = core::cmp::max(memory_start, linear_mappable_start);
        if physical_start >= physical_end {
            continue;
        }
        map_swapper_linear_memory(mode, linear_physical_base, physical_start, physical_end)?;
    }

    let dtb_pa = VM.early_dtb_pa.load(Ordering::Acquire) as usize;
    if dtb_pa != 0 {
        map_swapper_region(
            mode,
            mode.fix_fdt_va(),
            dtb_pa & PMD_MASK,
            2 * PMD_SIZE,
            PAGE_KERNEL_FLAGS,
            &SWAPPER_PAGE_TABLE.fixmap_path,
        )?;
        SWAPPER_PAGE_TABLE
            .fixmap_va
            .store(mode.fix_fdt_va() as u64, Ordering::Relaxed);
    }

    map_swapper_kernel(mode)?;
    page_table_write_fence();

    let root_pa = runtime_physical_address(SWAPPER_PAGE_TABLE.root.physical_address())?;
    let ppn = (root_pa as u64) >> PAGE_SHIFT;
    if ppn & !SATP_PPN_MASK != 0 {
        return Err(VmSetupError::AddressOverflow);
    }
    let satp = mode.satp_bits() | ppn;
    // Publish all PTE stores before making the new root active, then flush the
    // complete translation cache as required by the SATP transition.
    unsafe {
        asm!("sfence.vma zero, zero", "csrw satp, {satp}", "sfence.vma zero, zero", satp = in(reg) satp, options(nostack));
    }
    SWAPPER_PAGE_TABLE
        .mode
        .store(mode as u64, Ordering::Relaxed);
    let (linear_va, linear_pa) = first_memory.unwrap_or((mode.page_offset(), 0));
    SWAPPER_PAGE_TABLE
        .linear_va
        .store(linear_va as u64, Ordering::Relaxed);
    SWAPPER_PAGE_TABLE
        .linear_pa
        .store(linear_pa, Ordering::Relaxed);
    SWAPPER_PAGE_TABLE
        .linear_flags
        .store(PAGE_KERNEL_FLAGS, Ordering::Relaxed);
    SWAPPER_PAGE_TABLE
        .kernel_va
        .store(VM.kernel_map.virtual_address() as u64, Ordering::Relaxed);
    SWAPPER_PAGE_TABLE
        .kernel_pa
        .store(VM.kernel_map.physical_address() as u64, Ordering::Relaxed);
    SWAPPER_PAGE_TABLE.kernel_flags.store(
        PTE_VALID | PTE_READ | PTE_EXEC | PTE_GLOBAL | PTE_ACCESSED | PTE_DIRTY,
        Ordering::Relaxed,
    );
    SWAPPER_PAGE_TABLE
        .fixmap_cleared
        .store(1, Ordering::Relaxed);
    SWAPPER_PAGE_TABLE.satp_switched.store(1, Ordering::Relaxed);
    SWAPPER_PAGE_TABLE
        .tlb_flush_completed
        .store(1, Ordering::Relaxed);
    pt_ops_set_late();
    swapper_checkpoints::swapper_online(SWAPPER_PAGE_TABLE.observation());
    if swapper_content_checkpoints::ENABLED {
        swapper_content_checkpoints::swapper_content(
            SWAPPER_PAGE_TABLE.content_observation(memblock, mode),
        );
    }
    Ok(())
}

pub(crate) fn setup_vm_final(memblock: &mut MemBlock) -> VmResult<()> {
    SwapperPageTable::enable(memblock)
}

/// Returns the DTB's already-established read-only early mapping.
pub(crate) fn early_dtb_mapping() -> Option<EarlyDtbMapping> {
    VM.early_dtb_mapping()
}

/// Returns the physical kernel-image range committed by `KernelMapType::preset`.
pub(crate) fn kernel_image_physical_range() -> Option<(u64, u64)> {
    let base = VM.kernel_map.physical_address.load(Ordering::Acquire);
    let size = VM.kernel_map.image_size.load(Ordering::Acquire);
    (base != 0 && size != 0 && base.checked_add(size).is_some()).then_some((base, size))
}

// SAFETY: this is the sole definition of the linker-visible `setup_vm` symbol,
// and its C ABI matches the MMU-off call site in the naked boot entry.
#[unsafe(export_name = "setup_vm")]
pub(crate) extern "C" fn setup_vm(dtb_pa: usize) -> usize {
    match VM
        .preset(dtb_pa)
        .and_then(|()| VM.setup(dtb_pa))
        .and_then(|()| VM.early_root_address())
    {
        Ok(root_address) => root_address,
        Err(error) => VM.fail_stop(error),
    }
}

const _: () = assert!(PAGE_TABLE_ENTRIES == 512);
const _: () = assert!(size_of::<AtomicU64>() == size_of::<u64>());
const _: () = assert!(align_of::<AtomicU64>() >= align_of::<u64>());
const _: () = assert!(size_of::<PageTablePage>() == PAGE_SIZE);
const _: () = assert!(align_of::<PageTablePage>() == PAGE_SIZE);
const _: () = assert!(size_of::<PageTableType<NoFixmap>>() == 4 * PAGE_SIZE);
const _: () = assert!(size_of::<PageTableType<FixmapPages>>() == 7 * PAGE_SIZE);
const _: () = assert!(core::mem::offset_of!(PageTableType<NoFixmap>, root) == 0);
const _: () = assert!(PAGE_KERNEL_EXEC_FLAGS == 0xef);
const _: () = assert!(PAGE_KERNEL_FLAGS == 0xe7);
