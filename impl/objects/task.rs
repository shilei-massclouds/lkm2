//! Schedulable task representation and the boot task instance.

/// Minimal task carrier for the boot task.
///
/// The complete scheduler-facing layout will be added as the task model is
/// implemented. Keeping this type non-zero-sized gives the boot task a stable,
/// writable address in the kernel image in the meantime.
#[repr(C)]
pub(crate) struct Task {
    _model_state: usize,
}

/// Initial boot/idle task used as `current` during early kernel entry.
#[unsafe(export_name = "init_task")]
pub(crate) static mut BOOT_TASK: Task = Task { _model_state: 0 };
