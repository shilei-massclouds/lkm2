//! MMU-off early virtual-memory setup.

use core::arch::asm;
use core::mem::{align_of, size_of};
use core::sync::atomic::{AtomicU8, AtomicU32, AtomicU64, Ordering};

use crate::checkpoints::{
    self, EarlyDtbObservation, EarlyKernelObservation, KernelMapObservation, TrampolineObservation,
};

const PAGE_SHIFT: usize = 12;
const PAGE_SIZE: usize = 1 << PAGE_SHIFT;
const PAGE_TABLE_ENTRIES: usize = PAGE_SIZE / size_of::<u64>();
const PAGE_TABLE_INDEX_MASK: usize = PAGE_TABLE_ENTRIES - 1;

const PMD_SHIFT: usize = 21;
const PMD_SIZE: usize = 1 << PMD_SHIFT;
const PMD_MASK: usize = !(PMD_SIZE - 1);
const PUD_SHIFT: usize = 30;
const P4D_SHIFT: usize = 39;
const PGD_SHIFT_SV57: usize = 48;

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

const KERNEL_LINK_ADDR: usize = 0xffff_ffff_8000_0000;
const ADDRESS_SPACE_LAST_PAGE: usize = usize::MAX - (PAGE_SIZE - 1);

const PAGE_OFFSET_SV57: usize = 0xff60_0000_0000_0000;
const PAGE_OFFSET_SV48: usize = 0xffff_af80_0000_0000;
const PAGE_OFFSET_SV39: usize = 0xffff_ffd6_0000_0000;

const FIX_FDT_VA_SV57: usize = 0xff1b_ffff_fec0_0000;
const FIX_FDT_VA_SV48: usize = 0xffff_8d7f_fec0_0000;
const FIX_FDT_VA_SV39: usize = 0xffff_ffc4_fec0_0000;

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
enum VmSetupError {
    UnsupportedPagingMode = 1,
    InvalidAlignment = 2,
    AddressOverflow = 3,
    PageTableCapacityExceeded = 4,
    InvalidMappingRange = 5,
}

type VmResult<T> = Result<T, VmSetupError>;

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
struct NoFixmap;

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
struct PageTableType<F> {
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
    mode: AtomicU8,
    page_offset: AtomicU64,
    virtual_address: AtomicU64,
    physical_address: AtomicU64,
    image_size: AtomicU64,
    virtual_physical_offset: AtomicU64,
}

impl KernelMapType {
    const fn new() -> Self {
        Self {
            mode: AtomicU8::new(0),
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

        self.mode.store(0, Ordering::Relaxed);
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
        self.mode.store(mode as u8, Ordering::Release);
    }

    fn mode(&self) -> VmResult<PagingMode> {
        match self.mode.load(Ordering::Acquire) {
            value if value == PagingMode::Sv57 as u8 => Ok(PagingMode::Sv57),
            value if value == PagingMode::Sv48 as u8 => Ok(PagingMode::Sv48),
            value if value == PagingMode::Sv39 as u8 => Ok(PagingMode::Sv39),
            _ => Err(VmSetupError::UnsupportedPagingMode),
        }
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
    trampoline: PageTableType<NoFixmap>,
    early: PageTableType<FixmapPages>,
}

impl VmType {
    const fn new() -> Self {
        Self {
            kernel_map: KernelMapType::new(),
            early_dtb_pa: AtomicU64::new(0),
            early_dtb_va: AtomicU64::new(0),
            setup_error: AtomicU32::new(0),
            trampoline: PageTableType::new_trampoline(),
            early: PageTableType::new_early(),
        }
    }

    fn preset(&self, _dtb_pa: usize) -> VmResult<()> {
        self.setup_error.store(0, Ordering::Relaxed);
        self.early_dtb_pa.store(0, Ordering::Relaxed);
        self.early_dtb_va.store(0, Ordering::Relaxed);

        self.kernel_map.preset()?;
        self.trampoline.preset();
        self.early.preset();

        let mode = self.probe_paging_mode()?;
        self.kernel_map.setup(mode);
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
        let mode = self.kernel_map.mode()?;
        self.trampoline.preset();
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

    fn probe_paging_mode(&self) -> VmResult<PagingMode> {
        for mode in [PagingMode::Sv57, PagingMode::Sv48, PagingMode::Sv39] {
            if probe_candidate(&self.early, mode)? {
                return Ok(mode);
            }
        }
        Err(VmSetupError::UnsupportedPagingMode)
    }

    fn setup_trampoline(&self, mode: PagingMode) -> VmResult<()> {
        self.trampoline.map_kernel_2m(
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
        let (leaf, path_ok) = self.trampoline.observe_kernel_leaf(mode, virtual_address);
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

static VM: VmType = VmType::new();

// SAFETY: this is the sole definition of the linker-visible `setup_vm` symbol,
// and its C ABI matches the MMU-off call site in the naked boot entry.
#[unsafe(export_name = "setup_vm")]
pub(crate) extern "C" fn setup_vm(dtb_pa: usize) {
    if let Err(error) = VM.preset(dtb_pa).and_then(|()| VM.setup(dtb_pa)) {
        VM.fail_stop(error);
    }
}

const _: () = assert!(PAGE_TABLE_ENTRIES == 512);
const _: () = assert!(size_of::<PageTablePage>() == PAGE_SIZE);
const _: () = assert!(align_of::<PageTablePage>() == PAGE_SIZE);
const _: () = assert!(size_of::<PageTableType<NoFixmap>>() == 4 * PAGE_SIZE);
const _: () = assert!(size_of::<PageTableType<FixmapPages>>() == 7 * PAGE_SIZE);
const _: () = assert!(PAGE_KERNEL_EXEC_FLAGS == 0xef);
const _: () = assert!(PAGE_KERNEL_FLAGS == 0xe7);
