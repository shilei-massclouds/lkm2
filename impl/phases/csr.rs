const SR_FS: usize = 0b11 << 13;
const SR_VS: usize = 0b11 << 9;

pub(crate) const SR_FS_VS: usize = SR_FS | SR_VS;
