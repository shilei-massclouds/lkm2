# ArchHead 编码约束

本页对应 [`model/phases/arch_head.spec`](../../model/phases/arch_head.spec)，实现位于
[`impl/phases/arch_head.rs`](../../impl/phases/arch_head.rs)。

当前 RISC-V 64 位机器入口按以下顺序建立 MMU-off 执行环境：

- 由专用的 `.head.text.entry` 输入 section 导出唯一的全局函数符号 `_start`；
- 屏蔽中断并清理 pending interrupt，使用 PC-relative 方式装载 global pointer；
- 禁用 S-mode FPU 和 Vector 状态，并在 flat image 入口清零 BSS；
- 保存固件传入的 boot hart id，建立 boot task 的 `tp` 和内核栈 `sp`；
- 保留固件在 `a1` 中传入的 DTB PA，并在调用前移动到 C ABI 的第一个参数 `a0`；
- 在进入 Rust VM 代码前把 `stvec` 指向本地永久停驻入口，使早期异常 fail-stop；
- 调用导出名和 ABI 均固定的 `extern "C" fn setup_vm(usize)`。

`setup_vm` 内部依次对应模型的 `Vm.Preset` 和 `Vm.Setup`，具体规则见
[`../objects/vm.md`](../objects/vm.md)。成功返回时仍处于 MMU-off，且 `satp == 0`；失败
路径不会返回。当前实现尚未进入 `StartKernel`，所以成功返回后也落入同一个本地
`wfi` fail-stop 循环。这只是未实现后续阶段的边界，不是 ArchHead “仅进入 WFI”，也
不是 `setup_vm` 永久启用 MMU 的授权。

入口的裸汇编是必要的 `unsafe` 边界。寄存器约定、栈有效性、PC-relative 符号访问和
控制流不返回条件必须由紧邻的 `SAFETY` 说明覆盖。固定 sibling `../linux-6.12` 只用于
核对 RISC-V 64 位机制和顺序；不得复制其 Linux image header、汇编实现、重定位流程或
永久 MMU 切换，也不得使 sibling 成为构建依赖。
