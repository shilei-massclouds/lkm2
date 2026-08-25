# ArchHead 编码约束

本页对应 [`model/phases/arch_head.spec`](../../model/phases/arch_head.spec)，实现位于
[`impl/phases/arch_head.rs`](../../impl/phases/arch_head.rs)。

当前 RISC-V 64 位机器入口按以下顺序建立 MMU-off 执行环境：

- 由专用的 `.head.text.entry` 输入 section 导出唯一固件入口 `_start`；其可执行的首个
  半字固定为 `0x5a4d`（字节为 `MZ`，同时解码为 `c.li s4, -13`），随后直接跳转
  `_start_kernel`；
- `_start_kernel` 是独立的全局 ELF 函数符号，位于 `.head.text`，承接实际的早期初始化；
- 屏蔽中断并清理 pending interrupt，使用 PC-relative 方式装载 global pointer；
- 禁用 S-mode FPU 和 Vector 状态，并在 flat image 入口清零 BSS；
- 保存固件传入的 boot hart id，建立 boot task 的 `tp` 和内核栈 `sp`；
- 保留固件在 `a1` 中传入的 DTB PA，并在调用前移动到 C ABI 的第一个参数 `a0`；
- 在进入 Rust VM 代码前把 `stvec` 指向本地永久停驻入口，使早期异常 fail-stop；
- 调用导出名和 ABI 均固定的 `extern "C" fn setup_vm(usize) -> usize`；返回值是最终
  EarlyPageTable root 的运行时物理地址。

`setup_vm` 内部依次对应模型的 `Vm.Preset` 和 `Vm.Setup`，具体规则见
[`../objects/vm.md`](../objects/vm.md)。成功返回时仍处于 MMU-off，且 `satp == 0`；失败
路径不会返回。当前实现尚未进入 `StartKernel`，所以成功返回后调用
`relocate_enable_mmu`，其已启用的前缀最终仍落入本地 `wfi` fail-stop 循环。这只是未实现
后续阶段的边界，不是 ArchHead “仅进入 WFI”，也不是 `setup_vm` 永久启用 MMU 的授权。

除 `_start` 和 `_start_kernel` 外，`.head.text` 还导出 naked C ABI 函数
`relocate_enable_mmu(a0 = page-table root)`，为未来的 Linux 风格地址重定位保留接口。
当前 `_start_kernel` 在 `setup_vm` 成功后调用它；已启用的前缀先按
`KERNEL_LINK_ADDR - runtime(_start)` 调整 `ra`，再通过显式屏障跳转到共享的私有 park
入口。屏障后的预备代码从独立全局 `satp_mode` 读取所选 SATP MODE，并与 `a0` 的 root
PPN 组合，但在实现完整切换前保持不可达。当前路径仍不得写入 `satp`、启用 MMU 或返回。
`_start` 仍是 linker entry 和唯一固件入口，不扩展为完整 Linux image/EFI header。

链接脚本必须断言 `_start == ADDR(.head.text)` 且 `SIZEOF(.head.text) <= 2M`，使整个
`.head.text` 都位于 trampoline 从内核链接基址开始建立的首个 2 MiB 映射内。当前源码
按 `_start`、`relocate_enable_mmu`、`_start_kernel`、私有 park 入口的顺序排列；该顺序
是源码布局偏好和当前产物检查项，不是由 linker 强制的长期 ABI。

这些独立入口函数的裸汇编是必要的 `unsafe` 边界。寄存器约定、栈有效性、PC-relative
符号访问和控制流不返回条件必须由紧邻的 `SAFETY` 说明覆盖。固定 sibling
`../linux-6.12` 只用于核对 RISC-V 64 位机制和顺序；不得复制其 Linux image header、
汇编实现、重定位流程或永久 MMU 切换，也不得使 sibling 成为构建依赖。
