//! Logical CPU to hardware hart mapping.

use crate::config::NR_CPUS;

pub(crate) static mut CPUID_TO_HARTID_MAP: [usize; NR_CPUS] = [0; NR_CPUS];
