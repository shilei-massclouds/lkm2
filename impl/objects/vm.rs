//! Early virtual-memory setup entry point.

pub(crate) struct VmType;

pub(crate) static VM: VmType = VmType;

impl VmType {
    pub(crate) fn setup(&self, _dtb_pa: usize) {}
}

#[unsafe(export_name = "setup_vm")]
pub(crate) extern "C" fn setup_vm(dtb_pa: usize) {
    VM.setup(dtb_pa);
}
