//! Logical CPU to hardware hart mapping.

pub(crate) const NR_CPUS: usize = 1;

pub(crate) static mut CPUID_TO_HARTID_MAP: [usize; NR_CPUS] = [0; NR_CPUS];
