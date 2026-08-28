//! Boot command-line validation and the link-time early-console registry.

use core::mem::{align_of, size_of};

pub(crate) const BOOT_COMMAND_LINE_CAPACITY: usize = 4096;
const EARLYCON_NAME_CAPACITY: usize = 16;

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(crate) enum BootCommandLineError {
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
    pub(crate) fn from_chosen_bootargs(value: &[u8]) -> Result<Self, BootCommandLineError> {
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

    fn parse_bootargs(value: &[u8]) -> Result<BootCommandLine, BootCommandLineError> {
        BootCommandLine::from_chosen_bootargs(value)
    }

    #[test]
    fn parses_valid_bootargs_and_other_arguments() {
        let command_line = parse_bootargs(b"root=/dev/vda earlycon=sbi quiet\0").unwrap();
        assert_eq!(command_line.as_str(), "root=/dev/vda earlycon=sbi quiet");
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
