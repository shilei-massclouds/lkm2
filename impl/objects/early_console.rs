//! DT boot command-line input and the link-time early-console registry.

use core::mem::{align_of, size_of};

pub(crate) const BOOT_COMMAND_LINE_CAPACITY: usize = 4096;
const FDT_HEADER_SIZE: usize = 40;
const FDT_MAGIC: u32 = 0xd00d_feed;
const FDT_BEGIN_NODE: u32 = 1;
const FDT_END_NODE: u32 = 2;
const FDT_PROP: u32 = 3;
const FDT_NOP: u32 = 4;
const FDT_END: u32 = 9;
const EARLYCON_NAME_CAPACITY: usize = 16;

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(crate) enum BootCommandLineError {
    InvalidHeader,
    InvalidRange,
    InvalidStructure,
    MissingBootArgs,
    DuplicateBootArgs,
    InvalidBootArgs,
    BootArgsTooLong,
    MissingEarlyCon,
    DuplicateEarlyCon,
    UnsupportedEarlyCon,
}

#[derive(Clone, Debug)]
pub(crate) struct BootCommandLine {
    bytes: [u8; BOOT_COMMAND_LINE_CAPACITY],
    len: usize,
}

impl BootCommandLine {
    pub(crate) fn from_dtb(dtb_window: &[u8]) -> Result<Self, BootCommandLineError> {
        let bootargs = Fdt::parse(dtb_window)?.bootargs()?;
        Self::from_bootargs_property(bootargs)
    }

    fn from_bootargs_property(value: &[u8]) -> Result<Self, BootCommandLineError> {
        if value.len() > BOOT_COMMAND_LINE_CAPACITY {
            return Err(BootCommandLineError::BootArgsTooLong);
        }
        let Some((&0, command_line)) = value.split_last() else {
            return Err(BootCommandLineError::InvalidBootArgs);
        };
        if command_line.contains(&0) {
            return Err(BootCommandLineError::InvalidBootArgs);
        }
        let command_line = core::str::from_utf8(command_line)
            .map_err(|_| BootCommandLineError::InvalidBootArgs)?;

        let mut earlycon = None;
        for token in command_line.split_ascii_whitespace() {
            let Some(value) = token.strip_prefix("earlycon=") else {
                continue;
            };
            if earlycon.replace(value).is_some() {
                return Err(BootCommandLineError::DuplicateEarlyCon);
            }
        }
        match earlycon {
            None => return Err(BootCommandLineError::MissingEarlyCon),
            Some("sbi") => {}
            Some(_) => return Err(BootCommandLineError::UnsupportedEarlyCon),
        }

        let mut bytes = [0; BOOT_COMMAND_LINE_CAPACITY];
        bytes[..command_line.len()].copy_from_slice(command_line.as_bytes());
        Ok(Self {
            bytes,
            len: command_line.len(),
        })
    }

    pub(crate) fn as_str(&self) -> &str {
        // SAFETY: construction validates UTF-8 before copying the same bytes.
        unsafe { core::str::from_utf8_unchecked(&self.bytes[..self.len]) }
    }

    fn earlycon_name(&self) -> Result<&str, BootCommandLineError> {
        self.as_str()
            .split_ascii_whitespace()
            .find_map(|token| token.strip_prefix("earlycon="))
            .ok_or(BootCommandLineError::MissingEarlyCon)
    }
}

struct Fdt<'a> {
    blob: &'a [u8],
    structure: &'a [u8],
    strings: &'a [u8],
}

impl<'a> Fdt<'a> {
    fn parse(window: &'a [u8]) -> Result<Self, BootCommandLineError> {
        if window.len() < FDT_HEADER_SIZE || read_be_u32(window, 0)? != FDT_MAGIC {
            return Err(BootCommandLineError::InvalidHeader);
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
            return Err(BootCommandLineError::InvalidHeader);
        }

        let structure_range = checked_range(structure_offset, structure_size, total_size)?;
        let strings_range = checked_range(strings_offset, strings_size, total_size)?;
        if structure_range.start < FDT_HEADER_SIZE
            || strings_range.start < FDT_HEADER_SIZE
            || ranges_overlap(&structure_range, &strings_range)
        {
            return Err(BootCommandLineError::InvalidRange);
        }

        let reserve_limit = structure_offset.min(strings_offset);
        if reserve_offset < FDT_HEADER_SIZE || reserve_offset >= reserve_limit {
            return Err(BootCommandLineError::InvalidRange);
        }
        let mut reserve_cursor = reserve_offset;
        loop {
            let entry_end = reserve_cursor
                .checked_add(16)
                .filter(|end| *end <= reserve_limit)
                .ok_or(BootCommandLineError::InvalidRange)?;
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

    fn bootargs(&self) -> Result<&'a [u8], BootCommandLineError> {
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
                        return Err(BootCommandLineError::InvalidStructure);
                    }
                    let (name, next) = nul_terminated(self.structure, cursor)?;
                    cursor = align_up_4(next)?;
                    if cursor > self.structure.len()
                        || (depth == 0 && (!name.is_empty() || root_seen))
                    {
                        return Err(BootCommandLineError::InvalidStructure);
                    }
                    if depth == 0 {
                        root_seen = true;
                    } else if depth == 1 && name == b"chosen" {
                        chosen_depth = Some(2);
                    }
                    depth = depth
                        .checked_add(1)
                        .ok_or(BootCommandLineError::InvalidStructure)?;
                }
                FDT_END_NODE => {
                    if depth == 0 {
                        return Err(BootCommandLineError::InvalidStructure);
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
                        return Err(BootCommandLineError::InvalidStructure);
                    }
                    let value_len = read_be_u32(self.structure, cursor)? as usize;
                    let name_offset = read_be_u32(self.structure, cursor + 4)? as usize;
                    cursor += 8;
                    let value_end = cursor
                        .checked_add(value_len)
                        .ok_or(BootCommandLineError::InvalidStructure)?;
                    let next = align_up_4(value_end)?;
                    if next > self.structure.len() {
                        return Err(BootCommandLineError::InvalidStructure);
                    }
                    let (name, _) = nul_terminated(self.strings, name_offset)?;
                    if chosen_depth == Some(depth) && name == b"bootargs" {
                        if bootargs.is_some() {
                            return Err(BootCommandLineError::DuplicateBootArgs);
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
                        return Err(BootCommandLineError::InvalidStructure);
                    }
                    return bootargs.ok_or(BootCommandLineError::MissingBootArgs);
                }
                _ => return Err(BootCommandLineError::InvalidStructure),
            }
        }
        Err(BootCommandLineError::InvalidStructure)
    }
}

fn read_be_u32(bytes: &[u8], offset: usize) -> Result<u32, BootCommandLineError> {
    let value = bytes
        .get(offset..offset + 4)
        .ok_or(BootCommandLineError::InvalidRange)?;
    Ok(u32::from_be_bytes(value.try_into().unwrap()))
}

fn read_be_u64(bytes: &[u8], offset: usize) -> Result<u64, BootCommandLineError> {
    let value = bytes
        .get(offset..offset + 8)
        .ok_or(BootCommandLineError::InvalidRange)?;
    Ok(u64::from_be_bytes(value.try_into().unwrap()))
}

fn checked_range(
    offset: usize,
    len: usize,
    total_size: usize,
) -> Result<core::ops::Range<usize>, BootCommandLineError> {
    let end = offset
        .checked_add(len)
        .filter(|end| *end <= total_size)
        .ok_or(BootCommandLineError::InvalidRange)?;
    Ok(offset..end)
}

fn ranges_overlap(left: &core::ops::Range<usize>, right: &core::ops::Range<usize>) -> bool {
    left.start < right.end && right.start < left.end
}

fn nul_terminated(bytes: &[u8], offset: usize) -> Result<(&[u8], usize), BootCommandLineError> {
    let tail = bytes
        .get(offset..)
        .ok_or(BootCommandLineError::InvalidStructure)?;
    let end = tail
        .iter()
        .position(|byte| *byte == 0)
        .ok_or(BootCommandLineError::InvalidStructure)?;
    Ok((&tail[..end], offset + end + 1))
}

fn align_up_4(value: usize) -> Result<usize, BootCommandLineError> {
    value
        .checked_add(3)
        .map(|value| value & !3)
        .ok_or(BootCommandLineError::InvalidStructure)
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
#[repr(u8)]
pub(crate) enum EarlyConsoleBackend {
    Sbi = 1,
}

#[derive(Clone, Copy)]
#[repr(C, align(8))]
struct EarlyConDescriptor {
    name_len: u8,
    backend: EarlyConsoleBackend,
    reserved: [u8; 6],
    name: [u8; EARLYCON_NAME_CAPACITY],
}

impl EarlyConDescriptor {
    const fn sbi() -> Self {
        let mut name = [0; EARLYCON_NAME_CAPACITY];
        name[0] = b's';
        name[1] = b'b';
        name[2] = b'i';
        Self {
            name_len: 3,
            backend: EarlyConsoleBackend::Sbi,
            reserved: [0; 6],
            name,
        }
    }

    fn name(&self) -> Option<&str> {
        let len = usize::from(self.name_len);
        let bytes = self.name.get(..len)?;
        core::str::from_utf8(bytes).ok()
    }
}

#[used]
#[unsafe(link_section = ".earlycon.table")]
static SBI_EARLY_CONSOLE: EarlyConDescriptor = EarlyConDescriptor::sbi();

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(crate) enum EarlyConLookupError {
    #[cfg(not(test))]
    MalformedTable,
    Missing,
    Ambiguous,
}

fn lookup_descriptors(
    descriptors: &[EarlyConDescriptor],
    name: &str,
) -> Result<EarlyConsoleBackend, EarlyConLookupError> {
    let mut found = None;
    for descriptor in descriptors {
        if descriptor.name() == Some(name) {
            if found.is_some() {
                return Err(EarlyConLookupError::Ambiguous);
            }
            found = Some(descriptor.backend);
        }
    }
    found.ok_or(EarlyConLookupError::Missing)
}

fn lookup_backend(
    command_line: &BootCommandLine,
    descriptors: &[EarlyConDescriptor],
) -> Result<EarlyConsoleBackend, EarlyConLookupError> {
    let name = command_line
        .earlycon_name()
        .map_err(|_| EarlyConLookupError::Missing)?;
    lookup_descriptors(descriptors, name)
}

#[cfg(not(test))]
unsafe extern "C" {
    static __earlycon_table_start: u8;
    static __earlycon_table_end: u8;
}

#[cfg(not(test))]
fn linked_descriptors() -> Result<&'static [EarlyConDescriptor], EarlyConLookupError> {
    let start = core::ptr::addr_of!(__earlycon_table_start) as usize;
    let end = core::ptr::addr_of!(__earlycon_table_end) as usize;
    let bytes = end
        .checked_sub(start)
        .ok_or(EarlyConLookupError::MalformedTable)?;
    if !start.is_multiple_of(align_of::<EarlyConDescriptor>())
        || !bytes.is_multiple_of(size_of::<EarlyConDescriptor>())
    {
        return Err(EarlyConLookupError::MalformedTable);
    }
    // SAFETY: the linker script brackets only aligned `EarlyConDescriptor`
    // entries retained from `.earlycon.table`, and the byte count was checked.
    Ok(unsafe {
        core::slice::from_raw_parts(
            start as *const EarlyConDescriptor,
            bytes / size_of::<EarlyConDescriptor>(),
        )
    })
}

#[cfg(not(test))]
pub(crate) fn lookup_linked_backend(
    command_line: &BootCommandLine,
) -> Result<EarlyConsoleBackend, EarlyConLookupError> {
    lookup_backend(command_line, linked_descriptors()?)
}

const _: () = assert!(size_of::<EarlyConDescriptor>() == 24);
const _: () = assert!(align_of::<EarlyConDescriptor>() == 8);

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

    fn parse_bootargs(value: &[u8]) -> Result<BootCommandLine, BootCommandLineError> {
        BootCommandLine::from_dtb(&make_fdt(&[value]))
    }

    #[test]
    fn parses_valid_fdt_and_other_arguments() {
        let command_line = parse_bootargs(b"root=/dev/vda earlycon=sbi quiet\0").unwrap();
        assert_eq!(command_line.as_str(), "root=/dev/vda earlycon=sbi quiet");
    }

    #[test]
    fn rejects_bad_header_and_structure_token() {
        let mut bad_magic = make_fdt(&[b"earlycon=sbi\0"]);
        bad_magic[0] = 0;
        assert_eq!(
            BootCommandLine::from_dtb(&bad_magic).unwrap_err(),
            BootCommandLineError::InvalidHeader
        );

        let mut bad_token = make_fdt(&[b"earlycon=sbi\0"]);
        let structure_offset = u32::from_be_bytes(bad_token[8..12].try_into().unwrap()) as usize;
        bad_token[structure_offset..structure_offset + 4].copy_from_slice(&7_u32.to_be_bytes());
        assert_eq!(
            BootCommandLine::from_dtb(&bad_token).unwrap_err(),
            BootCommandLineError::InvalidStructure
        );
    }

    #[test]
    fn rejects_out_of_bounds_offsets_and_sizes() {
        let mut bad_offset = make_fdt(&[b"earlycon=sbi\0"]);
        let out_of_bounds = ((bad_offset.len() + 3) & !3) as u32;
        bad_offset[8..12].copy_from_slice(&out_of_bounds.to_be_bytes());
        assert_eq!(
            BootCommandLine::from_dtb(&bad_offset).unwrap_err(),
            BootCommandLineError::InvalidRange
        );

        let mut bad_size = make_fdt(&[b"earlycon=sbi\0"]);
        bad_size[36..40].copy_from_slice(&u32::MAX.to_be_bytes());
        assert_eq!(
            BootCommandLine::from_dtb(&bad_size).unwrap_err(),
            BootCommandLineError::InvalidRange
        );
    }

    #[test]
    fn rejects_missing_and_duplicate_bootargs() {
        assert_eq!(
            BootCommandLine::from_dtb(&make_fdt(&[])).unwrap_err(),
            BootCommandLineError::MissingBootArgs
        );
        assert_eq!(
            BootCommandLine::from_dtb(&make_fdt(&[b"earlycon=sbi\0", b"earlycon=sbi\0"]))
                .unwrap_err(),
            BootCommandLineError::DuplicateBootArgs
        );
    }

    #[test]
    fn validates_bootargs_termination_utf8_and_capacity() {
        assert_eq!(
            parse_bootargs(b"earlycon=sbi").unwrap_err(),
            BootCommandLineError::InvalidBootArgs
        );
        assert_eq!(
            parse_bootargs(b"earlycon=sbi\xff\0").unwrap_err(),
            BootCommandLineError::InvalidBootArgs
        );
        let oversized = vec![b'x'; BOOT_COMMAND_LINE_CAPACITY + 1];
        assert_eq!(
            parse_bootargs(&oversized).unwrap_err(),
            BootCommandLineError::BootArgsTooLong
        );
    }

    #[test]
    fn requires_one_exact_sbi_token() {
        for missing in [
            b"quiet\0".as_slice(),
            b"foo=earlycon=sbi\0",
            b"myearlycon=sbi\0",
        ] {
            assert_eq!(
                parse_bootargs(missing).unwrap_err(),
                BootCommandLineError::MissingEarlyCon
            );
        }
        for unsupported in [b"earlycon=uart\0".as_slice(), b"earlycon=sbi,0\0"] {
            assert_eq!(
                parse_bootargs(unsupported).unwrap_err(),
                BootCommandLineError::UnsupportedEarlyCon
            );
        }
        assert_eq!(
            parse_bootargs(b"earlycon=sbi earlycon=sbi\0").unwrap_err(),
            BootCommandLineError::DuplicateEarlyCon
        );
    }

    #[test]
    fn registry_lookup_is_unique() {
        let sbi = EarlyConDescriptor::sbi();
        let command_line = parse_bootargs(b"earlycon=sbi\0").unwrap();
        assert_eq!(
            lookup_backend(&command_line, &[sbi]),
            Ok(EarlyConsoleBackend::Sbi)
        );
        assert_eq!(
            lookup_descriptors(&[sbi], "uart"),
            Err(EarlyConLookupError::Missing)
        );
        assert_eq!(
            lookup_descriptors(&[sbi, sbi], "sbi"),
            Err(EarlyConLookupError::Ambiguous)
        );
    }
}
