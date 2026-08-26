//! Minimal SBI v2 DBCN capability and early-console backend.

const SBI_EXT_BASE: usize = 0x10;
const SBI_EXT_BASE_GET_SPEC_VERSION: usize = 0;
const SBI_EXT_BASE_PROBE_EXT: usize = 3;
const SBI_EXT_DBCN: usize = 0x4442_434e;
const SBI_EXT_DBCN_CONSOLE_WRITE_BYTE: usize = 2;
const SBI_SPEC_VERSION_2_0: usize = 2 << 24;

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(crate) struct SbiRet {
    pub(crate) error: isize,
    pub(crate) value: isize,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(crate) struct SbiCallArgs {
    pub(crate) extension: usize,
    pub(crate) function: usize,
    pub(crate) arguments: [usize; 6],
}

impl SbiCallArgs {
    const fn new(extension: usize, function: usize, arguments: [usize; 6]) -> Self {
        Self {
            extension,
            function,
            arguments,
        }
    }
}

pub(crate) trait SbiCall {
    fn call(&mut self, args: SbiCallArgs) -> SbiRet;
}

pub(crate) struct Ecall;

#[cfg(target_arch = "riscv64")]
impl SbiCall for Ecall {
    fn call(&mut self, args: SbiCallArgs) -> SbiRet {
        let mut a0 = args.arguments[0];
        let mut a1 = args.arguments[1];
        // SAFETY: the SBI calling convention assigns a0-a5 to arguments, a6
        // to the function ID, and a7 to the extension ID. `ecall` returns the
        // signed error/value bit patterns in a0/a1 and preserves the Rust stack.
        unsafe {
            core::arch::asm!(
                "ecall",
                inlateout("a0") a0,
                inlateout("a1") a1,
                in("a2") args.arguments[2],
                in("a3") args.arguments[3],
                in("a4") args.arguments[4],
                in("a5") args.arguments[5],
                in("a6") args.function,
                in("a7") args.extension,
                options(nostack),
            );
        }
        SbiRet {
            error: a0 as isize,
            value: a1 as isize,
        }
    }
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(crate) enum SbiProbeError {
    CallFailed { function: usize, error: isize },
    InvalidSpecVersion,
}

/// A successfully constructed value means the SBI capability probe completed.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(crate) struct SbiCapability {
    spec_version: usize,
    dbcn_available: bool,
}

impl SbiCapability {
    pub(crate) fn probe<C: SbiCall + ?Sized>(call: &mut C) -> Result<Self, SbiProbeError> {
        let version_ret = call.call(SbiCallArgs::new(
            SBI_EXT_BASE,
            SBI_EXT_BASE_GET_SPEC_VERSION,
            [0; 6],
        ));
        if version_ret.error != 0 {
            return Err(SbiProbeError::CallFailed {
                function: SBI_EXT_BASE_GET_SPEC_VERSION,
                error: version_ret.error,
            });
        }
        let spec_version =
            usize::try_from(version_ret.value).map_err(|_| SbiProbeError::InvalidSpecVersion)?;
        if spec_version < SBI_SPEC_VERSION_2_0 {
            return Ok(Self {
                spec_version,
                dbcn_available: false,
            });
        }

        let probe_ret = call.call(SbiCallArgs::new(
            SBI_EXT_BASE,
            SBI_EXT_BASE_PROBE_EXT,
            [SBI_EXT_DBCN, 0, 0, 0, 0, 0],
        ));
        if probe_ret.error != 0 {
            return Err(SbiProbeError::CallFailed {
                function: SBI_EXT_BASE_PROBE_EXT,
                error: probe_ret.error,
            });
        }
        Ok(Self {
            spec_version,
            dbcn_available: probe_ret.value > 0,
        })
    }

    #[cfg(test)]
    const fn spec_version(self) -> usize {
        self.spec_version
    }

    pub(crate) const fn dbcn_available(self) -> bool {
        self.dbcn_available
    }
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(crate) enum SbiConsoleEnableError {
    DbcnUnavailable,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(crate) struct SbiWriteError {
    pub(crate) error: isize,
}

pub(crate) struct SbiConsole<C> {
    call: C,
}

impl<C: SbiCall> SbiConsole<C> {
    pub(crate) fn enable(
        capability: SbiCapability,
        call: C,
    ) -> Result<Self, SbiConsoleEnableError> {
        if !capability.dbcn_available() {
            return Err(SbiConsoleEnableError::DbcnUnavailable);
        }
        Ok(Self { call })
    }

    pub(crate) fn write_byte(&mut self, byte: u8) -> Result<(), SbiWriteError> {
        let ret = self.call.call(SbiCallArgs::new(
            SBI_EXT_DBCN,
            SBI_EXT_DBCN_CONSOLE_WRITE_BYTE,
            [usize::from(byte), 0, 0, 0, 0, 0],
        ));
        if ret.error != 0 {
            return Err(SbiWriteError { error: ret.error });
        }
        Ok(())
    }

    pub(crate) fn write(&mut self, bytes: &[u8]) -> Result<(), SbiWriteError> {
        for &byte in bytes {
            self.write_byte(byte)?;
        }
        Ok(())
    }
}

#[cfg(not(test))]
impl<C: SbiCall> crate::objects::printk::ConsoleWrite for SbiConsole<C> {
    type Error = SbiWriteError;

    fn write(&mut self, bytes: &[u8]) -> Result<(), Self::Error> {
        SbiConsole::write(self, bytes)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::collections::VecDeque;
    use std::vec::Vec;

    #[derive(Default)]
    struct FakeCall {
        responses: VecDeque<SbiRet>,
        calls: Vec<SbiCallArgs>,
    }

    impl FakeCall {
        fn with_responses(responses: &[SbiRet]) -> Self {
            Self {
                responses: responses.iter().copied().collect(),
                calls: Vec::new(),
            }
        }
    }

    impl SbiCall for FakeCall {
        fn call(&mut self, args: SbiCallArgs) -> SbiRet {
            self.calls.push(args);
            self.responses.pop_front().expect("unexpected SBI call")
        }
    }

    const fn success(value: isize) -> SbiRet {
        SbiRet { error: 0, value }
    }

    fn probe(responses: &[SbiRet]) -> (Result<SbiCapability, SbiProbeError>, FakeCall) {
        let mut call = FakeCall::with_responses(responses);
        let capability = SbiCapability::probe(&mut call);
        (capability, call)
    }

    #[test]
    fn version_call_failure_is_reported() {
        let _production_boundary_type = Ecall;
        let (result, call) = probe(&[SbiRet {
            error: -2,
            value: 0,
        }]);
        assert_eq!(
            result,
            Err(SbiProbeError::CallFailed {
                function: SBI_EXT_BASE_GET_SPEC_VERSION,
                error: -2
            })
        );
        assert_eq!(call.calls.len(), 1);
    }

    #[test]
    fn version_below_two_completes_without_dbcn() {
        let (result, call) = probe(&[success((1 << 24) | 9)]);
        let capability = result.unwrap();
        assert_eq!(capability.spec_version(), (1 << 24) | 9);
        assert!(!capability.dbcn_available());
        assert_eq!(call.calls.len(), 1);
    }

    #[test]
    fn zero_probe_completes_without_dbcn() {
        let (result, call) = probe(&[success(SBI_SPEC_VERSION_2_0 as isize), success(0)]);
        assert!(!result.unwrap().dbcn_available());
        assert_eq!(call.calls.len(), 2);
        assert_eq!(call.calls[1].extension, SBI_EXT_BASE);
        assert_eq!(call.calls[1].function, SBI_EXT_BASE_PROBE_EXT);
        assert_eq!(call.calls[1].arguments[0], SBI_EXT_DBCN);
    }

    #[test]
    fn probe_error_is_reported() {
        let (result, _) = probe(&[
            success(SBI_SPEC_VERSION_2_0 as isize),
            SbiRet {
                error: -1,
                value: 99,
            },
        ]);
        assert_eq!(
            result,
            Err(SbiProbeError::CallFailed {
                function: SBI_EXT_BASE_PROBE_EXT,
                error: -1
            })
        );
    }

    #[test]
    fn positive_probe_produces_dbcn_capability() {
        let (result, _) = probe(&[success(SBI_SPEC_VERSION_2_0 as isize), success(7)]);
        assert!(result.unwrap().dbcn_available());
    }

    #[test]
    fn console_rejects_completed_unavailable_capability() {
        let (capability, _) = probe(&[success(SBI_SPEC_VERSION_2_0 as isize), success(0)]);
        let result = SbiConsole::enable(capability.unwrap(), FakeCall::default());
        assert!(matches!(
            result,
            Err(SbiConsoleEnableError::DbcnUnavailable)
        ));
    }

    #[test]
    fn write_byte_uses_dbcn_arguments() {
        let (capability, _) = probe(&[success(SBI_SPEC_VERSION_2_0 as isize), success(1)]);
        let call = FakeCall::with_responses(&[success(0)]);
        let mut console = SbiConsole::enable(capability.unwrap(), call).unwrap();
        console.write_byte(b'K').unwrap();
        assert_eq!(
            console.call.calls,
            [SbiCallArgs::new(
                SBI_EXT_DBCN,
                SBI_EXT_DBCN_CONSOLE_WRITE_BYTE,
                [usize::from(b'K'), 0, 0, 0, 0, 0]
            )]
        );
    }

    #[test]
    fn write_stops_and_propagates_error() {
        let (capability, _) = probe(&[success(SBI_SPEC_VERSION_2_0 as isize), success(1)]);
        let call = FakeCall::with_responses(&[
            success(0),
            SbiRet {
                error: -4,
                value: 0,
            },
            success(0),
        ]);
        let mut console = SbiConsole::enable(capability.unwrap(), call).unwrap();
        assert_eq!(console.write(b"abc"), Err(SbiWriteError { error: -4 }));
        assert_eq!(console.call.calls.len(), 2);
        assert_eq!(console.call.calls[0].arguments[0], usize::from(b'a'));
        assert_eq!(console.call.calls[1].arguments[0], usize::from(b'b'));
    }
}
