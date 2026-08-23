# ArchHead 编码约束

本页对应 [`model/phases/arch_head.spec`](../../model/phases/arch_head.spec)，实现位于
[`impl/phases/arch_head.rs`](../../impl/phases/arch_head.rs)。

当前阶段只为 RISC-V 64 位建立真实机器入口边界：

- 由专用的 `.head.text.entry` 输入 section 导出唯一的全局函数符号 `_start`；
- `_start` 进入后以 `wfi` 和本地跳转永久停驻；
- 不输出信息，不关机，也不进入尚未实现的 `StartKernel`；
- 暂不实现 Linux image header、寄存器初始化、栈、重定位和 MMU。

后续补充入口内容时，以固定 sibling `../linux-6.12` 的 RISC-V 64 位实现为机制、
顺序和差分行为参考，但不复制其汇编实现。
