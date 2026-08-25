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
  EarlyPageTable root 的运行时物理地址；
- 将该 root 保留在 `a0` 调用 `relocate_enable_mmu`，完成 trampoline 到 early 页表的
  双阶段 SATP 切换，并返回虚拟地址调用点。

`setup_vm` 内部依次对应模型的 `Vm.Preset` 和 `Vm.Setup`，具体规则见
[`../objects/vm.md`](../objects/vm.md)。成功返回时仍处于 MMU-off，且 `satp == 0`；失败
路径不会返回。`setup_vm` 本身不会把 early root 留在 SATP 中；成功返回后的 ArchHead
重定位路径负责启用 early MMU。`relocate_enable_mmu` 返回后，当前 `_start_kernel` 仍进入
本地 `wfi` fail-stop 循环，作为尚未实现后续 `StartKernel` 代码的边界；该循环不再是重定位路径前的
不可达屏障。

除 `_start` 和 `_start_kernel` 外，`.head.text` 还导出 naked C ABI 函数
`relocate_enable_mmu(a0 = early page-table root)`。该函数按以下顺序执行完整重定位：

1. 按 `KERNEL_LINK_ADDR - runtime(_start)` 把 `ra` 调整到虚拟返回地址，并把四字节对齐的
   本地标签 `1:` 的虚拟地址写入 `stvec`；
2. 将 `a0` 的 early root 右移 `PAGE_SHIFT`，与唯一 `satp_mode` 的完整 MODE 位域组合并
   保存在寄存器中；
3. 取唯一 `trampoline_pg_dir` 符号的 MMU-off 运行时物理地址，同样右移
   `PAGE_SHIFT` 后构造第一个 SATP，执行全局 `sfence.vma` 并写入 SATP；
4. 从 trampoline 的虚拟 trap/fall-through 目标继续，先把 `stvec` 恢复为永久停驻入口，
   再在虚拟地址空间重载 `gp`；
5. 写入已保存的 early SATP，执行全局 `sfence.vma`，最后 `ret` 到虚拟调用点。

`trampoline_pg_dir` 和 `satp_mode` 是两个互不别名、各自唯一的 linker-visible Rust static；
前者的符号地址就是 4 KiB 对齐的 trampoline root 页地址，后者只保存运行时选择的 SATP
MODE 位域。`setup_vm` 仍只通过 `a0` 返回 early root，不额外导出 `early_pg_dir`。
`_start` 仍是 linker entry 和唯一固件入口，不扩展为完整 Linux image/EFI header。

链接脚本必须断言 `_start == ADDR(.head.text)` 且 `SIZEOF(.head.text) <= 2M`，使整个
`.head.text` 都位于 trampoline 从内核链接基址开始建立的首个 2 MiB 映射内。当前源码
按 `_start`、`relocate_enable_mmu`、`_start_kernel`、私有 park 入口的顺序排列；该顺序
是源码布局偏好和当前产物检查项，不是由 linker 强制的长期 ABI。

这些独立入口函数的裸汇编是必要的 `unsafe` 边界。`relocate_enable_mmu` 的 `SAFETY`
契约必须覆盖 early root 的页对齐与来源、两套页表的发布状态、`satp_mode` 的有效性，以及
两套映射对当前 `.head.text` 的覆盖。寄存器约定、栈有效性和 PC-relative 符号访问也必须
由紧邻说明覆盖。固定 sibling
`../linux-6.12` 只用于核对 RISC-V 64 位机制和顺序；不得复制其 Linux image header、
汇编实现、最终页表或其他启动策略，也不得使 sibling 成为构建依赖。
