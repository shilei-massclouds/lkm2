//! Firmware DTB input and strict `/chosen/bootargs` extraction.

const FDT_HEADER_SIZE: usize = 40;
const FDT_MAGIC: u32 = 0xd00d_feed;
const FDT_BEGIN_NODE: u32 = 1;
const FDT_END_NODE: u32 = 2;
const FDT_PROP: u32 = 3;
const FDT_NOP: u32 = 4;
const FDT_END: u32 = 9;

/// Read-only view of the part of the two-PMD fixmap beginning at the DTB.
#[derive(Clone, Copy)]
pub(crate) struct EarlyDtbMapping {
    virtual_address: usize,
    len: usize,
}

impl EarlyDtbMapping {
    pub(crate) const fn new(virtual_address: usize, len: usize) -> Self {
        Self {
            virtual_address,
            len,
        }
    }

    /// Returns the mapped bytes. The mapping remains owned by the static early
    /// page table and is never rewritten after `setup_vm` publishes it.
    pub(crate) fn as_bytes(self) -> &'static [u8] {
        // SAFETY: `VmType::early_dtb_mapping` constructs this view only after
        // both consecutive DTB PMDs have been installed. `len` is capped at
        // the remainder of those mappings and the early tables are static.
        unsafe { core::slice::from_raw_parts(self.virtual_address as *const u8, self.len) }
    }
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(crate) enum DtbBlobError {
    InvalidHeader,
    InvalidRange,
    InvalidStructure,
    MissingBootArgs,
    DuplicateBootArgs,
}

#[derive(Clone, Copy, Debug)]
pub(crate) struct DtbBlob<'a> {
    blob: &'a [u8],
    structure: &'a [u8],
    strings: &'a [u8],
}

impl<'a> DtbBlob<'a> {
    pub(crate) fn from_bytes(window: &'a [u8]) -> Result<Self, DtbBlobError> {
        if window.len() < FDT_HEADER_SIZE || read_be_u32(window, 0)? != FDT_MAGIC {
            return Err(DtbBlobError::InvalidHeader);
        }
        let total_size = read_be_u32(window, 4)? as usize;
        let structure_offset = read_be_u32(window, 8)? as usize;
        let strings_offset = read_be_u32(window, 12)? as usize;
        let reserve_offset = read_be_u32(window, 16)? as usize;
        let version = read_be_u32(window, 20)?;
        let last_compatible_version = read_be_u32(window, 24)?;
        let strings_size = read_be_u32(window, 32)? as usize;
        let structure_size = read_be_u32(window, 36)? as usize;

        if total_size < FDT_HEADER_SIZE
            || total_size > window.len()
            || version < 17
            || last_compatible_version > 17
            || last_compatible_version > version
            || structure_offset & 3 != 0
            || reserve_offset & 7 != 0
        {
            return Err(DtbBlobError::InvalidHeader);
        }

        let structure_range = checked_range(structure_offset, structure_size, total_size)?;
        let strings_range = checked_range(strings_offset, strings_size, total_size)?;
        if structure_range.start < FDT_HEADER_SIZE
            || strings_range.start < FDT_HEADER_SIZE
            || ranges_overlap(&structure_range, &strings_range)
        {
            return Err(DtbBlobError::InvalidRange);
        }

        let reserve_limit = structure_offset.min(strings_offset);
        if reserve_offset < FDT_HEADER_SIZE || reserve_offset >= reserve_limit {
            return Err(DtbBlobError::InvalidRange);
        }
        let mut reserve_cursor = reserve_offset;
        loop {
            let entry_end = reserve_cursor
                .checked_add(16)
                .filter(|end| *end <= reserve_limit)
                .ok_or(DtbBlobError::InvalidRange)?;
            let address = read_be_u64(window, reserve_cursor)?;
            let size = read_be_u64(window, reserve_cursor + 8)?;
            reserve_cursor = entry_end;
            if address == 0 && size == 0 {
                break;
            }
        }

        Ok(Self {
            blob: &window[..total_size],
            structure: &window[structure_range],
            strings: &window[strings_range],
        })
    }

    pub(crate) fn chosen_bootargs(&self) -> Result<&'a [u8], DtbBlobError> {
        let mut cursor = 0;
        let mut depth = 0_usize;
        let mut root_seen = false;
        let mut root_closed = false;
        let mut chosen_depth = None;
        let mut bootargs = None;

        while cursor < self.structure.len() {
            let token = read_be_u32(self.structure, cursor)?;
            cursor += 4;
            match token {
                FDT_BEGIN_NODE => {
                    if root_closed {
                        return Err(DtbBlobError::InvalidStructure);
                    }
                    let (name, next) = nul_terminated(self.structure, cursor)?;
                    cursor = align_up_4(next)?;
                    if cursor > self.structure.len()
                        || (depth == 0 && (!name.is_empty() || root_seen))
                    {
                        return Err(DtbBlobError::InvalidStructure);
                    }
                    if depth == 0 {
                        root_seen = true;
                    } else if depth == 1 && name == b"chosen" {
                        chosen_depth = Some(2);
                    }
                    depth = depth.checked_add(1).ok_or(DtbBlobError::InvalidStructure)?;
                }
                FDT_END_NODE => {
                    if depth == 0 {
                        return Err(DtbBlobError::InvalidStructure);
                    }
                    if chosen_depth == Some(depth) {
                        chosen_depth = None;
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
                            .is_none_or(|end| end > self.structure.len())
                    {
                        return Err(DtbBlobError::InvalidStructure);
                    }
                    let value_len = read_be_u32(self.structure, cursor)? as usize;
                    let name_offset = read_be_u32(self.structure, cursor + 4)? as usize;
                    cursor += 8;
                    let value_end = cursor
                        .checked_add(value_len)
                        .ok_or(DtbBlobError::InvalidStructure)?;
                    let next = align_up_4(value_end)?;
                    if next > self.structure.len() {
                        return Err(DtbBlobError::InvalidStructure);
                    }
                    let (name, _) = nul_terminated(self.strings, name_offset)?;
                    if chosen_depth == Some(depth) && name == b"bootargs" {
                        if bootargs.is_some() {
                            return Err(DtbBlobError::DuplicateBootArgs);
                        }
                        let blob_offset =
                            self.structure.as_ptr() as usize - self.blob.as_ptr() as usize + cursor;
                        bootargs = Some(&self.blob[blob_offset..blob_offset + value_len]);
                    }
                    cursor = next;
                }
                FDT_NOP => {}
                FDT_END => {
                    if !root_seen || !root_closed || depth != 0 || cursor != self.structure.len() {
                        return Err(DtbBlobError::InvalidStructure);
                    }
                    return bootargs.ok_or(DtbBlobError::MissingBootArgs);
                }
                _ => return Err(DtbBlobError::InvalidStructure),
            }
        }
        Err(DtbBlobError::InvalidStructure)
    }
}

fn read_be_u32(bytes: &[u8], offset: usize) -> Result<u32, DtbBlobError> {
    let value = bytes
        .get(offset..offset + 4)
        .ok_or(DtbBlobError::InvalidRange)?;
    Ok(u32::from_be_bytes(value.try_into().unwrap()))
}

fn read_be_u64(bytes: &[u8], offset: usize) -> Result<u64, DtbBlobError> {
    let value = bytes
        .get(offset..offset + 8)
        .ok_or(DtbBlobError::InvalidRange)?;
    Ok(u64::from_be_bytes(value.try_into().unwrap()))
}

fn checked_range(
    offset: usize,
    len: usize,
    total_size: usize,
) -> Result<core::ops::Range<usize>, DtbBlobError> {
    let end = offset
        .checked_add(len)
        .filter(|end| *end <= total_size)
        .ok_or(DtbBlobError::InvalidRange)?;
    Ok(offset..end)
}

fn ranges_overlap(left: &core::ops::Range<usize>, right: &core::ops::Range<usize>) -> bool {
    left.start < right.end && right.start < left.end
}

fn nul_terminated(bytes: &[u8], offset: usize) -> Result<(&[u8], usize), DtbBlobError> {
    let tail = bytes.get(offset..).ok_or(DtbBlobError::InvalidStructure)?;
    let end = tail
        .iter()
        .position(|byte| *byte == 0)
        .ok_or(DtbBlobError::InvalidStructure)?;
    Ok((&tail[..end], offset + end + 1))
}

fn align_up_4(value: usize) -> Result<usize, DtbBlobError> {
    value
        .checked_add(3)
        .map(|value| value & !3)
        .ok_or(DtbBlobError::InvalidStructure)
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::vec;
    use std::vec::Vec;

    fn push_u32(bytes: &mut Vec<u8>, value: u32) {
        bytes.extend_from_slice(&value.to_be_bytes());
    }

    fn begin_node(structure: &mut Vec<u8>, name: &[u8]) {
        push_u32(structure, FDT_BEGIN_NODE);
        structure.extend_from_slice(name);
        structure.push(0);
        while structure.len() & 3 != 0 {
            structure.push(0);
        }
    }

    fn property(structure: &mut Vec<u8>, name_offset: u32, value: &[u8]) {
        push_u32(structure, FDT_PROP);
        push_u32(structure, value.len() as u32);
        push_u32(structure, name_offset);
        structure.extend_from_slice(value);
        while structure.len() & 3 != 0 {
            structure.push(0);
        }
    }

    fn make_fdt(bootargs: &[&[u8]]) -> Vec<u8> {
        let mut structure = Vec::new();
        begin_node(&mut structure, b"");
        begin_node(&mut structure, b"chosen");
        for value in bootargs {
            property(&mut structure, 0, value);
        }
        push_u32(&mut structure, FDT_END_NODE);
        push_u32(&mut structure, FDT_END_NODE);
        push_u32(&mut structure, FDT_END);

        let strings = b"bootargs\0";
        let reserve_offset = FDT_HEADER_SIZE;
        let structure_offset = reserve_offset + 16;
        let strings_offset = structure_offset + structure.len();
        let total_size = strings_offset + strings.len();
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
            strings.len() as u32,
            structure.len() as u32,
        ];
        for (index, value) in header.into_iter().enumerate() {
            blob[index * 4..index * 4 + 4].copy_from_slice(&value.to_be_bytes());
        }
        blob[structure_offset..strings_offset].copy_from_slice(&structure);
        blob[strings_offset..].copy_from_slice(strings);
        blob
    }

    #[test]
    fn mapping_exposes_its_static_window() {
        static BYTES: &[u8] = b"dtb";
        let mapping = EarlyDtbMapping::new(BYTES.as_ptr() as usize, BYTES.len());
        assert_eq!(mapping.as_bytes(), BYTES);
    }

    #[test]
    fn extracts_unique_chosen_bootargs() {
        let blob = make_fdt(&[b"root=/dev/vda earlycon=sbi quiet\0"]);
        assert_eq!(
            DtbBlob::from_bytes(&blob).unwrap().chosen_bootargs(),
            Ok(b"root=/dev/vda earlycon=sbi quiet\0".as_slice())
        );
    }

    #[test]
    fn rejects_bad_header_and_structure_token() {
        let mut bad_magic = make_fdt(&[b"earlycon=sbi\0"]);
        bad_magic[0] = 0;
        assert_eq!(
            DtbBlob::from_bytes(&bad_magic).unwrap_err(),
            DtbBlobError::InvalidHeader
        );

        let mut bad_token = make_fdt(&[b"earlycon=sbi\0"]);
        let structure_offset = u32::from_be_bytes(bad_token[8..12].try_into().unwrap()) as usize;
        bad_token[structure_offset..structure_offset + 4].copy_from_slice(&7_u32.to_be_bytes());
        assert_eq!(
            DtbBlob::from_bytes(&bad_token).unwrap().chosen_bootargs(),
            Err(DtbBlobError::InvalidStructure)
        );
    }

    #[test]
    fn rejects_out_of_bounds_offsets_and_sizes() {
        let mut bad_offset = make_fdt(&[b"earlycon=sbi\0"]);
        let out_of_bounds = ((bad_offset.len() + 3) & !3) as u32;
        bad_offset[8..12].copy_from_slice(&out_of_bounds.to_be_bytes());
        assert_eq!(
            DtbBlob::from_bytes(&bad_offset).unwrap_err(),
            DtbBlobError::InvalidRange
        );

        let mut bad_size = make_fdt(&[b"earlycon=sbi\0"]);
        bad_size[36..40].copy_from_slice(&u32::MAX.to_be_bytes());
        assert_eq!(
            DtbBlob::from_bytes(&bad_size).unwrap_err(),
            DtbBlobError::InvalidRange
        );
    }

    #[test]
    fn rejects_missing_and_duplicate_bootargs() {
        assert_eq!(
            DtbBlob::from_bytes(&make_fdt(&[]))
                .unwrap()
                .chosen_bootargs(),
            Err(DtbBlobError::MissingBootArgs)
        );
        assert_eq!(
            DtbBlob::from_bytes(&make_fdt(&[b"earlycon=sbi\0", b"earlycon=sbi\0"]))
                .unwrap()
                .chosen_bootargs(),
            Err(DtbBlobError::DuplicateBootArgs)
        );
    }
}
