//! Single-hart, interrupt-masked early Printk buffering and console replay.

pub(crate) const EARLY_PRINTK_CAPACITY: usize = 4096;

pub(crate) trait ConsoleWrite {
    type Error;

    fn write(&mut self, bytes: &[u8]) -> Result<(), Self::Error>;
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(crate) enum PrintkError<E> {
    BufferOverflow,
    ConsoleWrite(E),
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(crate) enum ConsoleRegistrationError<E> {
    AlreadyRegistered,
    Replay(E),
}

/// Fixed early buffer. Its caller must be the sole boot hart with interrupts
/// masked; this deliberately provides no allocation, lock, or ring semantics.
pub(crate) struct EarlyPrintk<C> {
    buffer: [u8; EARLY_PRINTK_CAPACITY],
    len: usize,
    console: Option<C>,
}

impl<C: ConsoleWrite> EarlyPrintk<C> {
    pub(crate) const fn new() -> Self {
        Self {
            buffer: [0; EARLY_PRINTK_CAPACITY],
            len: 0,
            console: None,
        }
    }

    pub(crate) fn record(&mut self, bytes: &[u8]) -> Result<(), PrintkError<C::Error>> {
        if let Some(console) = &mut self.console {
            return console.write(bytes).map_err(PrintkError::ConsoleWrite);
        }

        let end = self
            .len
            .checked_add(bytes.len())
            .filter(|end| *end <= EARLY_PRINTK_CAPACITY)
            .ok_or(PrintkError::BufferOverflow)?;
        self.buffer[self.len..end].copy_from_slice(bytes);
        self.len = end;
        Ok(())
    }

    pub(crate) fn register_console(
        &mut self,
        mut console: C,
    ) -> Result<(), ConsoleRegistrationError<C::Error>> {
        if self.console.is_some() {
            return Err(ConsoleRegistrationError::AlreadyRegistered);
        }

        if self.len != 0 {
            console
                .write(&self.buffer[..self.len])
                .map_err(ConsoleRegistrationError::Replay)?;
        }

        self.buffer[..self.len].fill(0);
        self.len = 0;
        self.console = Some(console);
        Ok(())
    }

    #[cfg(test)]
    fn buffered(&self) -> &[u8] {
        &self.buffer[..self.len]
    }

    #[cfg(test)]
    fn console(&self) -> Option<&C> {
        self.console.as_ref()
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::vec::Vec;

    #[derive(Clone, Copy, Debug, PartialEq, Eq)]
    struct WriteFailure;

    #[derive(Default)]
    struct FakeConsole {
        writes: Vec<Vec<u8>>,
        fail_at: Option<usize>,
    }

    impl FakeConsole {
        fn failing_at(call: usize) -> Self {
            Self {
                writes: Vec::new(),
                fail_at: Some(call),
            }
        }
    }

    impl ConsoleWrite for FakeConsole {
        type Error = WriteFailure;

        fn write(&mut self, bytes: &[u8]) -> Result<(), Self::Error> {
            if self.fail_at == Some(self.writes.len()) {
                return Err(WriteFailure);
            }
            self.writes.push(bytes.to_vec());
            Ok(())
        }
    }

    #[test]
    fn replays_buffer_fifo_then_writes_directly() {
        let mut printk = EarlyPrintk::<FakeConsole>::new();
        printk.record(b"LKM2 ").unwrap();
        printk.record(b"kernel\n").unwrap();
        printk.register_console(FakeConsole::default()).unwrap();

        assert!(printk.buffered().is_empty());
        assert_eq!(printk.console().unwrap().writes, [b"LKM2 kernel\n"]);

        printk.record(b"online\n").unwrap();
        assert_eq!(
            printk.console().unwrap().writes,
            [b"LKM2 kernel\n".as_slice(), b"online\n".as_slice()]
        );
    }

    #[test]
    fn overflow_is_atomic() {
        let mut printk = EarlyPrintk::<FakeConsole>::new();
        let full = [b'x'; EARLY_PRINTK_CAPACITY];
        printk.record(&full).unwrap();
        assert_eq!(printk.record(b"!"), Err(PrintkError::BufferOverflow));
        assert_eq!(printk.buffered(), full);
        assert!(printk.console().is_none());
    }

    #[test]
    fn successful_registration_happens_once() {
        let mut printk = EarlyPrintk::<FakeConsole>::new();
        printk.record(b"banner").unwrap();
        printk.register_console(FakeConsole::default()).unwrap();
        assert!(matches!(
            printk.register_console(FakeConsole::default()),
            Err(ConsoleRegistrationError::AlreadyRegistered)
        ));
        assert_eq!(printk.console().unwrap().writes, [b"banner"]);
    }

    #[test]
    fn replay_failure_does_not_commit_registration_or_clear_buffer() {
        let mut printk = EarlyPrintk::<FakeConsole>::new();
        printk.record(b"banner").unwrap();
        assert!(matches!(
            printk.register_console(FakeConsole::failing_at(0)),
            Err(ConsoleRegistrationError::Replay(WriteFailure))
        ));
        assert_eq!(printk.buffered(), b"banner");
        assert!(printk.console().is_none());

        printk.register_console(FakeConsole::default()).unwrap();
        assert_eq!(printk.console().unwrap().writes, [b"banner"]);
    }

    #[test]
    fn direct_write_failure_is_propagated_after_registration() {
        let mut printk = EarlyPrintk::<FakeConsole>::new();
        printk.register_console(FakeConsole::failing_at(1)).unwrap();
        assert_eq!(printk.record(b"first"), Ok(()));
        assert_eq!(
            printk.record(b"second"),
            Err(PrintkError::ConsoleWrite(WriteFailure))
        );
        assert!(printk.console().is_some());
    }
}
