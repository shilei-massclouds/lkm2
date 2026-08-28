//! Fixed-capacity early physical-memory discovery and required reservations.

use super::dtb_blob::DtbBlob;

const FDT_BEGIN_NODE: u32 = 1;
const FDT_END_NODE: u32 = 2;
const FDT_PROP: u32 = 3;
const FDT_NOP: u32 = 4;
const FDT_END: u32 = 9;

const MAX_MEMORY_REGIONS: usize = 16;
const MAX_RESERVED_REGIONS: usize = 64;

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(crate) enum MemBlockError {
    InvalidStructure,
    InvalidCells,
    InvalidMemory,
    MissingMemory,
    InvalidReservation,
    DynamicReservationUnsupported,
    RegionCapacityExceeded,
    AddressOverflow,
}

#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
struct PhysRange {
    base: u64,
    end: u64,
}

impl PhysRange {
    fn from_base_size(base: u64, size: u64) -> Result<Self, MemBlockError> {
        if size == 0 {
            return Err(MemBlockError::AddressOverflow);
        }
        let end = base
            .checked_add(size)
            .ok_or(MemBlockError::AddressOverflow)?;
        Ok(Self { base, end })
    }
}

#[derive(Clone, Copy)]
struct RegionSet<const CAPACITY: usize> {
    regions: [PhysRange; CAPACITY],
    len: usize,
}

impl<const CAPACITY: usize> RegionSet<CAPACITY> {
    const fn new() -> Self {
        Self {
            regions: [PhysRange { base: 0, end: 0 }; CAPACITY],
            len: 0,
        }
    }

    fn add(&mut self, range: PhysRange) -> Result<(), MemBlockError> {
        let mut first = 0;
        while first < self.len && self.regions[first].end < range.base {
            first += 1;
        }

        let mut merged = range;
        let mut after = first;
        while after < self.len && self.regions[after].base <= merged.end {
            merged.base = merged.base.min(self.regions[after].base);
            merged.end = merged.end.max(self.regions[after].end);
            after += 1;
        }

        if first == after {
            if self.len == CAPACITY {
                return Err(MemBlockError::RegionCapacityExceeded);
            }
            self.regions.copy_within(first..self.len, first + 1);
            self.regions[first] = merged;
            self.len += 1;
            return Ok(());
        }

        self.regions[first] = merged;
        let removed = after - first - 1;
        if removed != 0 {
            self.regions.copy_within(after..self.len, first + 1);
            self.len -= removed;
        }
        Ok(())
    }

    const fn len(&self) -> usize {
        self.len
    }
}

#[derive(Clone, Copy)]
struct Property<'a> {
    value: Option<&'a [u8]>,
    duplicate: bool,
}

impl<'a> Property<'a> {
    const EMPTY: Self = Self {
        value: None,
        duplicate: false,
    };

    fn record(&mut self, value: &'a [u8]) {
        if self.value.replace(value).is_some() {
            self.duplicate = true;
        }
    }

    fn unique(self) -> Result<Option<&'a [u8]>, MemBlockError> {
        if self.duplicate {
            Err(MemBlockError::InvalidStructure)
        } else {
            Ok(self.value)
        }
    }
}

#[derive(Clone, Copy)]
struct Node<'a> {
    name: &'a [u8],
    address_cells: Property<'a>,
    size_cells: Property<'a>,
    device_type: Property<'a>,
    reg: Property<'a>,
    ranges: Property<'a>,
    size: Property<'a>,
    status: Property<'a>,
}

impl<'a> Node<'a> {
    const EMPTY: Self = Self {
        name: &[],
        address_cells: Property::EMPTY,
        size_cells: Property::EMPTY,
        device_type: Property::EMPTY,
        reg: Property::EMPTY,
        ranges: Property::EMPTY,
        size: Property::EMPTY,
        status: Property::EMPTY,
    };

    fn new(name: &'a [u8]) -> Self {
        Self {
            name,
            ..Self::EMPTY
        }
    }

    fn record_property(&mut self, name: &[u8], value: &'a [u8]) {
        match name {
            b"#address-cells" => self.address_cells.record(value),
            b"#size-cells" => self.size_cells.record(value),
            b"device_type" => self.device_type.record(value),
            b"reg" => self.reg.record(value),
            b"ranges" => self.ranges.record(value),
            b"size" => self.size.record(value),
            b"status" => self.status.record(value),
            _ => {}
        }
    }

    fn enabled(self) -> Result<bool, MemBlockError> {
        let Some(status) = self.status.unique()? else {
            return Ok(true);
        };
        Ok(matches!(single_string(status)?, b"okay" | b"ok"))
    }
}

#[derive(Clone, Copy, PartialEq, Eq)]
enum ScanTarget {
    Memory,
    Reserved,
}

pub(crate) struct MemBlockMemory {
    regions: RegionSet<MAX_MEMORY_REGIONS>,
}

impl MemBlockMemory {
    pub(crate) fn derive_from_dtb(dtb: &DtbBlob<'_>) -> Result<Self, MemBlockError> {
        let mut regions = RegionSet::new();
        let mut unused_reserved = RegionSet::new();
        scan_structure(dtb, ScanTarget::Memory, &mut regions, &mut unused_reserved)?;
        if regions.len() == 0 {
            return Err(MemBlockError::MissingMemory);
        }
        Ok(Self { regions })
    }

    pub(crate) const fn region_count(&self) -> usize {
        self.regions.len()
    }
}

pub(crate) struct MemBlockReserved {
    regions: RegionSet<MAX_RESERVED_REGIONS>,
}

impl MemBlockReserved {
    fn complete(
        memory: &MemBlockMemory,
        dtb: &DtbBlob<'_>,
        dtb_physical_address: u64,
        kernel_image: (u64, u64),
    ) -> Result<Self, MemBlockError> {
        if memory.region_count() == 0 {
            return Err(MemBlockError::MissingMemory);
        }

        let mut regions = RegionSet::new();
        regions.add(PhysRange::from_base_size(kernel_image.0, kernel_image.1)?)?;
        add_reserve_map(dtb, &mut regions)?;
        let mut unused_memory = RegionSet::new();
        scan_structure(dtb, ScanTarget::Reserved, &mut unused_memory, &mut regions)?;
        let dtb_size =
            u64::try_from(dtb.total_size()).map_err(|_| MemBlockError::AddressOverflow)?;
        regions.add(PhysRange::from_base_size(dtb_physical_address, dtb_size)?)?;
        Ok(Self { regions })
    }

    pub(crate) const fn region_count(&self) -> usize {
        self.regions.len()
    }
}

pub(crate) struct MemBlock {
    memory: MemBlockMemory,
    reserved: MemBlockReserved,
}

impl MemBlock {
    pub(crate) fn setup_bootmem(
        memory: MemBlockMemory,
        dtb: &DtbBlob<'_>,
        dtb_physical_address: u64,
        kernel_image: (u64, u64),
    ) -> Result<Self, MemBlockError> {
        let reserved =
            MemBlockReserved::complete(&memory, dtb, dtb_physical_address, kernel_image)?;
        Ok(Self { memory, reserved })
    }

    pub(crate) const fn memory_region_count(&self) -> usize {
        self.memory.region_count()
    }

    pub(crate) const fn reserved_region_count(&self) -> usize {
        self.reserved.region_count()
    }
}

fn scan_structure(
    dtb: &DtbBlob<'_>,
    target: ScanTarget,
    memory: &mut RegionSet<MAX_MEMORY_REGIONS>,
    reserved: &mut RegionSet<MAX_RESERVED_REGIONS>,
) -> Result<(), MemBlockError> {
    let structure = dtb.structure_block();
    let strings = dtb.strings_block();
    let mut cursor = 0;
    let mut depth = 0_usize;
    let mut root_seen = false;
    let mut root_closed = false;
    let mut reserved_root_seen = false;
    let mut root = Node::EMPTY;
    let mut level_two = Node::EMPTY;
    let mut level_three = Node::EMPTY;

    while cursor < structure.len() {
        let token = read_u32(structure, cursor)?;
        cursor += 4;
        match token {
            FDT_BEGIN_NODE => {
                if root_closed {
                    return Err(MemBlockError::InvalidStructure);
                }
                let (name, next) = nul_terminated(structure, cursor)?;
                cursor = align_up_4(next)?;
                if cursor > structure.len() || (depth == 0 && (!name.is_empty() || root_seen)) {
                    return Err(MemBlockError::InvalidStructure);
                }
                depth = depth
                    .checked_add(1)
                    .ok_or(MemBlockError::InvalidStructure)?;
                match depth {
                    1 => {
                        root_seen = true;
                        root = Node::new(name);
                    }
                    2 => level_two = Node::new(name),
                    3 => level_three = Node::new(name),
                    _ => {}
                }
            }
            FDT_END_NODE => {
                if depth == 0 {
                    return Err(MemBlockError::InvalidStructure);
                }
                match (target, depth) {
                    (ScanTarget::Memory, 2) => process_memory_node(root, level_two, memory)?,
                    (ScanTarget::Reserved, 3) if level_two.name == b"reserved-memory" => {
                        process_reserved_child(level_two, level_three, reserved)?;
                    }
                    (ScanTarget::Reserved, 2) if level_two.name == b"reserved-memory" => {
                        if reserved_root_seen {
                            return Err(MemBlockError::InvalidReservation);
                        }
                        validate_reserved_root(level_two)?;
                        reserved_root_seen = true;
                    }
                    _ => {}
                }
                depth -= 1;
                if depth == 0 {
                    root_closed = true;
                }
            }
            FDT_PROP => {
                if depth == 0
                    || cursor
                        .checked_add(8)
                        .is_none_or(|end| end > structure.len())
                {
                    return Err(MemBlockError::InvalidStructure);
                }
                let value_len = read_u32(structure, cursor)? as usize;
                let name_offset = read_u32(structure, cursor + 4)? as usize;
                cursor += 8;
                let value_end = cursor
                    .checked_add(value_len)
                    .ok_or(MemBlockError::InvalidStructure)?;
                let next = align_up_4(value_end)?;
                if next > structure.len() {
                    return Err(MemBlockError::InvalidStructure);
                }
                let (name, _) = nul_terminated(strings, name_offset)?;
                let value = &structure[cursor..value_end];
                match depth {
                    1 => root.record_property(name, value),
                    2 => level_two.record_property(name, value),
                    3 => level_three.record_property(name, value),
                    _ => {}
                }
                cursor = next;
            }
            FDT_NOP => {}
            FDT_END => {
                if !root_seen || !root_closed || depth != 0 || cursor != structure.len() {
                    return Err(MemBlockError::InvalidStructure);
                }
                return Ok(());
            }
            _ => return Err(MemBlockError::InvalidStructure),
        }
    }
    Err(MemBlockError::InvalidStructure)
}

fn process_memory_node(
    root: Node<'_>,
    node: Node<'_>,
    memory: &mut RegionSet<MAX_MEMORY_REGIONS>,
) -> Result<(), MemBlockError> {
    let Some(device_type) = node.device_type.unique()? else {
        return Ok(());
    };
    if single_string(device_type)? != b"memory" {
        return Ok(());
    }
    if !node.enabled()? {
        return Ok(());
    }
    let (address_cells, size_cells) = node_cells(root, (2, 1))?;
    let reg = node.reg.unique()?.ok_or(MemBlockError::InvalidMemory)?;
    add_reg_ranges(reg, address_cells, size_cells, memory).map_err(|error| match error {
        MemBlockError::InvalidReservation => MemBlockError::InvalidMemory,
        other => other,
    })
}

fn validate_reserved_root(node: Node<'_>) -> Result<(), MemBlockError> {
    if !node.enabled()? {
        return Ok(());
    }
    if node.address_cells.unique()?.is_none() || node.size_cells.unique()?.is_none() {
        return Err(MemBlockError::InvalidReservation);
    }
    node_cells(node, (2, 1))?;
    let ranges = node
        .ranges
        .unique()?
        .ok_or(MemBlockError::InvalidReservation)?;
    if !ranges.is_empty() {
        return Err(MemBlockError::InvalidReservation);
    }
    Ok(())
}

fn process_reserved_child(
    parent: Node<'_>,
    node: Node<'_>,
    reserved: &mut RegionSet<MAX_RESERVED_REGIONS>,
) -> Result<(), MemBlockError> {
    if !parent.enabled()? || !node.enabled()? {
        return Ok(());
    }
    let (address_cells, size_cells) = node_cells(parent, (2, 1))?;
    if let Some(reg) = node.reg.unique()? {
        return add_reg_ranges(reg, address_cells, size_cells, reserved);
    }
    if node.size.unique()?.is_some() {
        return Err(MemBlockError::DynamicReservationUnsupported);
    }
    Err(MemBlockError::InvalidReservation)
}

fn node_cells(node: Node<'_>, defaults: (u32, u32)) -> Result<(u32, u32), MemBlockError> {
    let address_cells = cell_count(node.address_cells.unique()?, defaults.0)?;
    let size_cells = cell_count(node.size_cells.unique()?, defaults.1)?;
    Ok((address_cells, size_cells))
}

fn cell_count(value: Option<&[u8]>, default: u32) -> Result<u32, MemBlockError> {
    let value = match value {
        Some(bytes) if bytes.len() == 4 => u32::from_be_bytes(bytes.try_into().unwrap()),
        Some(_) => return Err(MemBlockError::InvalidCells),
        None => default,
    };
    if matches!(value, 1 | 2) {
        Ok(value)
    } else {
        Err(MemBlockError::InvalidCells)
    }
}

fn add_reg_ranges<const CAPACITY: usize>(
    reg: &[u8],
    address_cells: u32,
    size_cells: u32,
    regions: &mut RegionSet<CAPACITY>,
) -> Result<(), MemBlockError> {
    let tuple_cells = address_cells
        .checked_add(size_cells)
        .ok_or(MemBlockError::InvalidCells)? as usize;
    let tuple_size = tuple_cells
        .checked_mul(4)
        .ok_or(MemBlockError::InvalidCells)?;
    if reg.is_empty() || !reg.len().is_multiple_of(tuple_size) {
        return Err(MemBlockError::InvalidReservation);
    }
    for tuple in reg.chunks_exact(tuple_size) {
        let base = read_cells(tuple, 0, address_cells)?;
        let size = read_cells(tuple, address_cells as usize * 4, size_cells)?;
        regions.add(PhysRange::from_base_size(base, size)?)?;
    }
    Ok(())
}

fn read_cells(bytes: &[u8], offset: usize, cells: u32) -> Result<u64, MemBlockError> {
    let mut value = 0_u64;
    for index in 0..cells as usize {
        value = (value << 32) | u64::from(read_u32(bytes, offset + index * 4)?);
    }
    Ok(value)
}

fn add_reserve_map(
    dtb: &DtbBlob<'_>,
    regions: &mut RegionSet<MAX_RESERVED_REGIONS>,
) -> Result<(), MemBlockError> {
    let bytes = dtb.reserve_map();
    if bytes.len() < 16 || !bytes.len().is_multiple_of(16) {
        return Err(MemBlockError::InvalidReservation);
    }
    let mut cursor = 0;
    while cursor < bytes.len() {
        let base = read_u64(bytes, cursor)?;
        let size = read_u64(bytes, cursor + 8)?;
        cursor += 16;
        if base == 0 && size == 0 {
            return (cursor == bytes.len())
                .then_some(())
                .ok_or(MemBlockError::InvalidReservation);
        }
        regions.add(PhysRange::from_base_size(base, size)?)?;
    }
    Err(MemBlockError::InvalidReservation)
}

fn read_u32(bytes: &[u8], offset: usize) -> Result<u32, MemBlockError> {
    let value = bytes
        .get(offset..offset + 4)
        .ok_or(MemBlockError::InvalidStructure)?;
    Ok(u32::from_be_bytes(value.try_into().unwrap()))
}

fn read_u64(bytes: &[u8], offset: usize) -> Result<u64, MemBlockError> {
    let value = bytes
        .get(offset..offset + 8)
        .ok_or(MemBlockError::InvalidReservation)?;
    Ok(u64::from_be_bytes(value.try_into().unwrap()))
}

fn nul_terminated(bytes: &[u8], offset: usize) -> Result<(&[u8], usize), MemBlockError> {
    let tail = bytes.get(offset..).ok_or(MemBlockError::InvalidStructure)?;
    let end = tail
        .iter()
        .position(|byte| *byte == 0)
        .ok_or(MemBlockError::InvalidStructure)?;
    Ok((&tail[..end], offset + end + 1))
}

fn single_string(value: &[u8]) -> Result<&[u8], MemBlockError> {
    let Some((&0, text)) = value.split_last() else {
        return Err(MemBlockError::InvalidStructure);
    };
    if text.contains(&0) {
        return Err(MemBlockError::InvalidStructure);
    }
    Ok(text)
}

fn align_up_4(value: usize) -> Result<usize, MemBlockError> {
    value
        .checked_add(3)
        .map(|value| value & !3)
        .ok_or(MemBlockError::InvalidStructure)
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::vec;
    use std::vec::Vec;

    const FDT_MAGIC: u32 = 0xd00d_feed;
    const FDT_HEADER_SIZE: usize = 40;
    const STRINGS: &[u8] =
        b"#address-cells\0#size-cells\0device_type\0reg\0ranges\0size\0bootargs\0";

    fn push_u32(bytes: &mut Vec<u8>, value: u32) {
        bytes.extend_from_slice(&value.to_be_bytes());
    }

    fn push_u64(bytes: &mut Vec<u8>, value: u64) {
        bytes.extend_from_slice(&value.to_be_bytes());
    }

    fn string_offset(name: &[u8]) -> u32 {
        STRINGS
            .windows(name.len() + 1)
            .position(|window| &window[..name.len()] == name && window[name.len()] == 0)
            .unwrap() as u32
    }

    fn begin_node(structure: &mut Vec<u8>, name: &[u8]) {
        push_u32(structure, FDT_BEGIN_NODE);
        structure.extend_from_slice(name);
        structure.push(0);
        while structure.len() & 3 != 0 {
            structure.push(0);
        }
    }

    fn property(structure: &mut Vec<u8>, name: &[u8], value: &[u8]) {
        push_u32(structure, FDT_PROP);
        push_u32(structure, value.len() as u32);
        push_u32(structure, string_offset(name));
        structure.extend_from_slice(value);
        while structure.len() & 3 != 0 {
            structure.push(0);
        }
    }

    fn cells(value: u64) -> [u8; 8] {
        value.to_be_bytes()
    }

    fn make_fdt(memory: bool, dynamic_reservation: bool) -> Vec<u8> {
        let mut structure = Vec::new();
        begin_node(&mut structure, b"");
        property(&mut structure, b"#address-cells", &2_u32.to_be_bytes());
        property(&mut structure, b"#size-cells", &2_u32.to_be_bytes());

        if memory {
            begin_node(&mut structure, b"memory@80000000");
            property(&mut structure, b"device_type", b"memory\0");
            let mut reg = Vec::new();
            reg.extend_from_slice(&cells(0x8000_0000));
            reg.extend_from_slice(&cells(0x0800_0000));
            property(&mut structure, b"reg", &reg);
            push_u32(&mut structure, FDT_END_NODE);
        }

        begin_node(&mut structure, b"chosen");
        property(&mut structure, b"bootargs", b"earlycon=sbi\0");
        push_u32(&mut structure, FDT_END_NODE);

        begin_node(&mut structure, b"reserved-memory");
        property(&mut structure, b"#address-cells", &2_u32.to_be_bytes());
        property(&mut structure, b"#size-cells", &2_u32.to_be_bytes());
        property(&mut structure, b"ranges", &[]);
        begin_node(&mut structure, b"firmware@82000000");
        if dynamic_reservation {
            property(&mut structure, b"size", &cells(0x2000));
        } else {
            let mut reg = Vec::new();
            reg.extend_from_slice(&cells(0x8200_0000));
            reg.extend_from_slice(&cells(0x2000));
            property(&mut structure, b"reg", &reg);
        }
        push_u32(&mut structure, FDT_END_NODE);
        push_u32(&mut structure, FDT_END_NODE);
        push_u32(&mut structure, FDT_END_NODE);
        push_u32(&mut structure, FDT_END);

        let mut reserve_map = Vec::new();
        push_u64(&mut reserve_map, 0x8100_0000);
        push_u64(&mut reserve_map, 0x1000);
        push_u64(&mut reserve_map, 0);
        push_u64(&mut reserve_map, 0);

        let reserve_offset = FDT_HEADER_SIZE;
        let structure_offset = reserve_offset + reserve_map.len();
        let strings_offset = structure_offset + structure.len();
        let total_size = strings_offset + STRINGS.len();
        let mut blob = vec![0; total_size];
        let header = [
            FDT_MAGIC,
            total_size as u32,
            structure_offset as u32,
            strings_offset as u32,
            reserve_offset as u32,
            17,
            16,
            0,
            STRINGS.len() as u32,
            structure.len() as u32,
        ];
        for (index, value) in header.into_iter().enumerate() {
            blob[index * 4..index * 4 + 4].copy_from_slice(&value.to_be_bytes());
        }
        blob[reserve_offset..structure_offset].copy_from_slice(&reserve_map);
        blob[structure_offset..strings_offset].copy_from_slice(&structure);
        blob[strings_offset..].copy_from_slice(STRINGS);
        blob
    }

    #[test]
    fn derives_memory_and_completes_all_required_reservations() {
        let bytes = make_fdt(true, false);
        let dtb = DtbBlob::from_bytes(&bytes).unwrap();
        assert_eq!(dtb.chosen_bootargs(), Ok(b"earlycon=sbi\0".as_slice()));
        let memory = MemBlockMemory::derive_from_dtb(&dtb).unwrap();
        assert_eq!(memory.region_count(), 1);
        let memblock =
            MemBlock::setup_bootmem(memory, &dtb, 0x8300_0000, (0x8020_0000, 0x10_0000)).unwrap();
        assert_eq!(memblock.memory_region_count(), 1);
        assert_eq!(memblock.reserved_region_count(), 4);
        assert_eq!(
            &memblock.reserved.regions.regions[..4],
            &[
                PhysRange {
                    base: 0x8020_0000,
                    end: 0x8030_0000,
                },
                PhysRange {
                    base: 0x8100_0000,
                    end: 0x8100_1000,
                },
                PhysRange {
                    base: 0x8200_0000,
                    end: 0x8200_2000,
                },
                PhysRange {
                    base: 0x8300_0000,
                    end: 0x8300_0000 + bytes.len() as u64,
                },
            ],
        );
    }

    #[test]
    fn rejects_missing_memory_before_setup_bootmem() {
        let bytes = make_fdt(false, false);
        let dtb = DtbBlob::from_bytes(&bytes).unwrap();
        assert!(matches!(
            MemBlockMemory::derive_from_dtb(&dtb),
            Err(MemBlockError::MissingMemory)
        ));
    }

    #[test]
    fn memory_survives_when_setup_bootmem_rejects_dynamic_reservation() {
        let bytes = make_fdt(true, true);
        let dtb = DtbBlob::from_bytes(&bytes).unwrap();
        let memory = MemBlockMemory::derive_from_dtb(&dtb).unwrap();
        assert_eq!(memory.region_count(), 1);
        assert!(matches!(
            MemBlockReserved::complete(&memory, &dtb, 0x8300_0000, (0x8020_0000, 0x10_0000),),
            Err(MemBlockError::DynamicReservationUnsupported)
        ));
        assert_eq!(memory.region_count(), 1);
    }

    #[test]
    fn region_set_sorts_and_coalesces_overlapping_or_adjacent_ranges() {
        let mut regions = RegionSet::<4>::new();
        regions
            .add(PhysRange::from_base_size(20, 10).unwrap())
            .unwrap();
        regions
            .add(PhysRange::from_base_size(0, 10).unwrap())
            .unwrap();
        regions
            .add(PhysRange::from_base_size(10, 10).unwrap())
            .unwrap();
        assert_eq!(regions.len(), 1);
        assert_eq!(regions.regions[0], PhysRange { base: 0, end: 30 });
    }
}
