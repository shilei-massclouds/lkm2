#[path = "../objects/dtb_blob.rs"]
mod dtb_blob;
#[path = "../objects/memblock.rs"]
mod memblock;
#[path = "../objects/page_allocator.rs"]
mod page_allocator;
#[path = "../objects/zone.rs"]
mod zone;

use page_allocator::PageAllocator;
#[path = "../objects/memory_node.rs"]
mod memory_node;

use dtb_blob::DtbBlob;
use memblock::{MemBlock, MemBlockMemory};
use memory_node::MemoryNode;
use std::vec;
use std::vec::Vec;

const FDT_MAGIC: u32 = 0xd00d_feed;
const FDT_HEADER_SIZE: usize = 40;
const STRINGS: &[u8] = b"#address-cells\0#size-cells\0device_type\0reg\0ranges\0";

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
    push_u32(structure, 1);
    structure.extend_from_slice(name);
    structure.push(0);
    while structure.len() & 3 != 0 {
        structure.push(0);
    }
}

fn property(structure: &mut Vec<u8>, name: &[u8], value: &[u8]) {
    push_u32(structure, 3);
    push_u32(structure, value.len() as u32);
    push_u32(structure, string_offset(name));
    structure.extend_from_slice(value);
    while structure.len() & 3 != 0 {
        structure.push(0);
    }
}

fn make_fdt(memory_ranges: &[(u64, u64)]) -> Vec<u8> {
    let mut structure = Vec::new();
    begin_node(&mut structure, b"");
    property(&mut structure, b"#address-cells", &2_u32.to_be_bytes());
    property(&mut structure, b"#size-cells", &2_u32.to_be_bytes());
    for (index, (base, size)) in memory_ranges.iter().copied().enumerate() {
        let name = if index == 0 {
            b"memory@0".to_vec()
        } else {
            format!("memory@{base:x}").into_bytes()
        };
        begin_node(&mut structure, &name);
        property(&mut structure, b"device_type", b"memory\0");
        let mut reg = Vec::new();
        push_u64(&mut reg, base);
        push_u64(&mut reg, size);
        property(&mut structure, b"reg", &reg);
        push_u32(&mut structure, 2);
    }
    push_u32(&mut structure, 2);
    push_u32(&mut structure, 9);

    let mut reserve_map = Vec::new();
    push_u64(&mut reserve_map, 0x8020_0000);
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

fn memblock(memory_ranges: &[(u64, u64)]) -> (MemBlock, Vec<u8>) {
    let bytes = make_fdt(memory_ranges);
    let dtb = DtbBlob::from_bytes(&bytes).unwrap();
    let memory = MemBlockMemory::derive_from_dtb(&dtb).unwrap();
    let block = MemBlock::setup_bootmem(memory, &dtb, 0x8040_0000, (0x8010_0000, 0x1000)).unwrap();
    (block, bytes)
}

#[test]
fn node_envelope_and_mem_map_follow_memblock() {
    let (block, _bytes) = memblock(&[(0x8000_1003, 0x2000), (0x9000_0000, 0x3000)]);
    let node = MemoryNode::initialize(&block).unwrap();
    assert_eq!(
        node.layout().physical_envelope(),
        (0x8000_1003, 0x9000_3000)
    );
    assert_eq!(node.layout().page_range(), (0x8000_2, 0x9000_3));
    let map = node.mem_map().unwrap();
    assert_eq!(map.envelope(), (0x8000_1003, 0x9000_3000));
    assert_eq!(map.page_range(), node.layout().page_range());
}

#[test]
fn reservations_are_removed_from_each_zone_managed_projection() {
    let (block, _bytes) = memblock(&[(0x8000_0000, 0x0800_0000)]);
    let node = MemoryNode::initialize(&block).unwrap();
    let zones = node.zones().unwrap();
    let managed: Vec<_> = zones.dma32().managed_ranges().collect();
    assert!(
        managed
            .iter()
            .all(|(start, end)| { *start >= 0x8000_0000 && *end <= 0x8800_0000 && *start < *end })
    );
    assert!(
        managed
            .iter()
            .all(|(start, end)| { *end <= 0x8010_0000 || *start >= 0x8010_1000 })
    );
    assert!(zones.dma32().free_area_initialized());
    assert!(zones.normal().free_area_initialized());
    assert!(zones.movable().free_area_initialized());
}

#[test]
fn zone_boundaries_and_fallback_filter_empty_zones() {
    let (block, _bytes) = memblock(&[(0x8000_0000, 0x5_0000_0000)]);
    let node = MemoryNode::initialize(&block).unwrap();
    let zones = node.zones().unwrap();
    assert_eq!(zones.dma32().envelope(), (0x8000_0000, 0x1_0000_0000));
    assert_eq!(zones.normal().envelope(), (0x1_0000_0000, 0x5_8000_0000));
    assert!(zones.movable().is_empty());
    assert_eq!(
        node.zone_lists().unwrap().iter().collect::<Vec<_>>(),
        vec![zone::ZoneKind::Normal, zone::ZoneKind::DMA32]
    );
}

#[test]
fn page_allocator_handoff_reserves_metadata_and_seeds_buddy() {
    let (mut block, _bytes) = memblock(&[(0x8000_0000, 0x0200_0000)]);
    let mut node = MemoryNode::initialize(&block).unwrap();
    let mut allocator = PageAllocator::new();
    allocator.enable(&mut node, &mut block).unwrap();

    assert!(allocator.is_online());
    assert!(node.is_allocator_online());
    assert!(block.free_all_completed());
    assert_eq!(block.metadata_region_count(), 2);
    assert!(block.metadata_ranges().all(|metadata| {
        let (start, end) = metadata.range();
        block
            .reserved_ranges()
            .any(|(reserved_start, reserved_end)| reserved_start <= start && end <= reserved_end)
    }));
    let metadata_kinds: Vec<_> = block
        .metadata_ranges()
        .map(|metadata| metadata.kind())
        .collect();
    assert_eq!(
        metadata_kinds,
        vec![
            memblock::MetadataKind::MemMap,
            memblock::MetadataKind::Buddy
        ]
    );
    assert!(matches!(
        block.allocate_phys(0x1000, 0x1000),
        Err(memblock::MemBlockError::HandoffComplete)
    ));

    let allocated = allocator.alloc_pages(&mut node, 0).unwrap();
    assert_eq!(allocated.order(), 0);
    assert_eq!(allocated.page_count(), 1);
    assert_eq!(allocated.zone(), zone::ZoneKind::DMA32);
    allocator.free(&mut node, allocated).unwrap();
    assert!(matches!(
        allocator.free(&mut node, allocated),
        Err(page_allocator::PageAllocatorError::Free(
            zone::BuddyFreeError::DoubleFree
        ))
    ));
}

#[test]
fn buddy_splits_and_coalesces_ordered_blocks() {
    let (mut block, _bytes) = memblock(&[(0x8000_0000, 0x0040_0000)]);
    let mut node = MemoryNode::initialize(&block).unwrap();
    let mut allocator = PageAllocator::new();
    allocator.enable(&mut node, &mut block).unwrap();

    let first = allocator.alloc_pages(&mut node, 2).unwrap();
    let second = allocator.alloc_pages(&mut node, 2).unwrap();
    assert_eq!(first.order(), 2);
    assert_eq!(second.order(), 2);
    assert_ne!(first.start_pfn(), second.start_pfn());
    allocator.free(&mut node, first).unwrap();
    allocator.free(&mut node, second).unwrap();
    let larger = allocator.alloc_pages(&mut node, 3).unwrap();
    assert_eq!(larger.order(), 3);
}

#[test]
fn allocator_falls_back_to_normal_and_rejects_bad_release_contracts() {
    let (mut block, _bytes) = memblock(&[(0x8000_0000, 0x5_0000_0000)]);
    let mut node = MemoryNode::initialize(&block).unwrap();
    let mut allocator = PageAllocator::new();
    allocator.enable(&mut node, &mut block).unwrap();

    let allocated = allocator.allocate_pages(&mut node, 4).unwrap();
    assert_eq!(allocated.zone(), zone::ZoneKind::Normal);
    assert!(matches!(
        allocator.release_pages(&mut node, allocated, 3),
        Err(zone::BuddyFreeError::InvalidOrder)
    ));
    allocator.release_pages(&mut node, allocated, 4).unwrap();
}
