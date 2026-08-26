# ArchHead 编码约束

本页对应 [`model/phases/arch_head.spec`](../../model/phases/arch_head.spec)，实现位于
[`impl/phases/arch_head.rs`](../../impl/phases/arch_head.rs)。

## Model-to-code 对照

`ArchHead.Action::Enter` 建模的是固件入口和 `StartKernel` 交接之间的稳定系统契约，不复刻
裸汇编的过程。其前置条件对应如下：

| Model 前置条件 | 实现事实 |
| --- | --- |
| `OpenSBI.state == Online` 与 `opensbi_kernel_entry_handoff_ready()` | OpenSBI 按 RISC-V boot ABI 跳入唯一 `_start`：`a0` 是 boot hart id，`a1` 是 DTB PA，入口 `satp` 为 Bare；`boot_entry` 的 `Safety` 契约冻结该边界。 |
| `CurrentCPU == BootCPU` 且 `BootCPU.state == Online` | `BootCPU.logical_id == 0` 表示当前唯一 boot CPU；CPU selector 从线路创建起可用，不依赖尚未发布的 current Task。 |
| `BootTask.state == OnCpu` | `Kernel.Enable` 已恢复 BootTask，随后经 `BootInitFlow` 同步进入 ArchHead；其 TaskFlow 已绑定 BootCPU，但 `CurrentTaskRef` 仍不可用。 |
| `KernelImage.state == Loaded` | flat kernel image 已由固件装载，BSS 尚待入口代码清零。 |
| `BootStack.state == Base` | 尚未建立供 `setup_vm` 使用的物理 stack，也未发布 early 虚拟 current-task stack。 |
| `Vm.state == Base` | `setup_vm` 尚未执行；该入口只允许从初始 VM 生命周期开始一次完整构造。 |

ArchHead 的 model drive 与实现顺序逐项对应：

| 顺序 | Model drive | 实现动作 |
| --- | --- | --- |
| 1 | `CurrentCPU.InterruptControlRef.MaskAll` | 清零 `sie`，屏蔽 S-mode interrupt；该动作不承担清 pending。 |
| 2 | `CurrentCPU.InterruptControlRef.ClearPending` | 独立写 `sip` 清理入口 pending interrupt。 |
| 3 | `KernelImage.Action::ClearBss` | flat image 路径清零 `__bss_start..__bss_stop`，令 KernelImage Loaded → Ready。 |
| 4 | `BootTask.Action::ResetCurrent` | 物理入口写 `tp = BOOT_TASK`，对应 bootstrap current 发布；不把 stack 初始化并入 ResetCurrent。 |
| 5 | `BootStack.Transition::Preset` | 建立 MMU-off 物理 `sp`，供 `setup_vm` 及重定位前调用使用。 |
| 6 | `Vm.Preset → Vm.Setup` | `setup_vm` 构造 trampoline 和 early 页表，返回最终 EarlyPageTable root。 |
| 7 | `BootStack.Transition::Setup` | early SATP 生效并重定位后重新加载虚拟 `tp`，建立虚拟 `sp = init_thread_union + THREAD_SIZE - PT_SIZE_ON_STACK`。 |

驱动完成后，KernelImage、BootStack 与 Vm 必须分别为 Ready，且
`CurrentTaskRef == BootTask`。ArchHead 再发布可由 StartKernel 独立检查的交接事实：

| Model 后置事实 | 实现事实 |
| --- | --- |
| `arch_head_interrupts_masked()` | `_start_kernel` 已清零 `sie`；结果快照中的 BootCPU interrupt mode 为 `Masked`。 |
| `arch_head_entry_pending_interrupts_cleared()` | `sip` 的入口 pending 位已清理；该保证与 mask 分开建立，结果快照 pending 为空。 |
| `arch_head_virtual_global_pointer_ready()` | MMU-off 阶段先以 PC-relative 方式加载 `gp`；trampoline 重定位后再次执行 `load_global_pointer!()`，交接时 `gp` 属于内核虚拟地址上下文。 |
| `arch_head_fpu_disabled()` / `arch_head_vector_disabled()` | `_start_kernel` 以 `SR_FS_VS` 分别清除 `sstatus.FS/VS`，交接时两者均为 Off。 |
| `arch_head_boot_hart_id_recorded()` | 固件 `a0` 被保存到唯一 `CPUID_TO_HARTID_MAP[0]`。 |
| `arch_head_current_task_reset()` | `tp` 已建立 BootTask current 身份，且 model 中 `CurrentTaskRef == BootTask`。 |
| `arch_head_early_address_space_active()` | `setup_vm` 发布 Ready 页表后，`relocate_enable_mmu` 最终把返回的 EarlyPageTable root 写入 `satp`，并在内核虚拟映射中返回。 |
| `arch_head_kernel_image_accessible()` | early 页表的 kernel window 覆盖 flat kernel image，虚拟 `gp`、代码和数据均可访问。 |
| `arch_head_firmware_fdt_accessible()` | 固件 `a1` 保持为 DTB PA 并传给 `setup_vm`；FDT 所在物理区经 early direct map 可访问。FDT 身份继续由 OpenSBI handoff/ABI 约束，不新增 model 对象。 |
| `arch_head_trap_context_ready()` | 最终 `setup_trap_vector` 将 `stvec` 安装为 early fail-stop trap target，并清零 `sscratch`。 |
| `arch_head_soc_early_init_complete()` | `soc_early_init` 已正常返回；紧随其后的 tail call 把控制流交给唯一 `start_kernel`。 |

`StartKernel.Action::Enter` 必须同时检查上述十二项事实、三对象 Ready 状态及
`CurrentTaskRef == BootTask`。`sip` 清除现在有独立的稳定事实，但 MMU-off 临时 park
trap、两次 `gp`、两阶段 `sp`、trampoline SATP、early SATP 与重定位指令的中间状态仍
只在本编码约束展开，不建立寄存器属性对象或额外过程型 Phase。此模型重构不改变现有
Rust 实现、导出符号、C ABI、checkpoint 调用和 QEMU debug 输出协议。

## Checkpoint 覆盖关系

现有 VM checkpoint 套件只覆盖 `setup_vm` 的 MMU-off 构表语义。Vm 完成 Setup 后的 Ready
关系、KernelMap/trampoline/early 页表 Ready invariant，以及 early kernel image 和 firmware
DTB 映射，均已由冻结的 28 个 VM canonical ID 覆盖。

ArchHead 自身不进入该 ID 集合：`CurrentTaskRef`、KernelImage/BSS、BootStack，以及 interrupt、
虚拟 `gp`、FS/VS、hart id、SATP 激活、trap context 和 SoC early init 等最终事实，继续由
derive、本文 coding 契约和实现测试验证。ArchHead 的 `establishes` 不生成实现 checkpoint，
其 scope 外的 `ensures` 也不得并入 VM 映射。

如果后续需要对这些最终硬件状态做运行时观测，应建立独立的 ArchHead checkpoint 套件及
观测 ABI 和验收入口，不扩展 `tools/checkpoints/vm.json`，也不改变既有 Linux `setup_vm`
差分协议。

## 入口与重定位顺序

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
  双阶段 SATP 切换，并返回虚拟地址调用点；
- 调用私有 naked `setup_trap_vector`，在 early 虚拟地址空间清零 `sscratch`，并把
  `stvec` 安装到当前安全的 fail-stop trap 入口；
- 在虚拟地址空间恢复 boot task 的 `tp` 和内核栈 `sp`，调用空的 `soc_early_init`，然后
  tail call `start_kernel` 完成 ArchHead 到 StartKernel 的控制流移交。

`setup_vm` 内部依次对应模型的 `Vm.Preset` 和 `Vm.Setup`，具体规则见
[`../objects/vm.md`](../objects/vm.md)。成功返回时仍处于 MMU-off，且 `satp == 0`；失败
路径不会返回。`setup_vm` 本身不会把 early root 留在 SATP 中；成功返回后的 ArchHead
重定位路径负责启用 early MMU。`relocate_enable_mmu` 返回后，当前 `_start_kernel` 仍进入
`setup_trap_vector`，恢复 C 环境并调用 `soc_early_init`，最后以 tail call 进入
`start_kernel`。当前 StartKernel 实现是不返回的空占位循环；ArchHead 不再在正常路径跳转
`secondary_park`。

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

`setup_trap_vector` 必须是位于 `.head.text` 的私有 naked C ABI helper。当前尚无
`handle_exception`，因此它把 `stvec` 指向同样位于 `.head.text` 的 `secondary_park`，并将
`sscratch` 清零后返回；正式异常入口落地时只替换 trap target，不改变调用 ABI。

`soc_early_init` 是 `impl/systems/kernel.rs` 中唯一导出的同名 C ABI 空函数，可以正常返回。
`start_kernel` 是 `impl/phases/start_kernel.rs` 中唯一导出的同名 C ABI 不返回函数，与模型
`StartKernel` 对应；当前只提供空占位循环，后续阶段在其内部逐步实现模型声明的启动序列。

`trampoline_pg_dir` 和 `satp_mode` 是两个互不别名、各自唯一的 linker-visible Rust static；
前者的符号地址就是 4 KiB 对齐的 trampoline root 页地址，后者只保存运行时选择的 SATP
MODE 位域。`setup_vm` 仍只通过 `a0` 返回 early root，不额外导出 `early_pg_dir`。
`_start` 仍是 linker entry 和唯一固件入口，不扩展为完整 Linux image/EFI header。

链接脚本必须断言 `_start == ADDR(.head.text)` 且 `SIZEOF(.head.text) <= 2M`，使整个
`.head.text` 都位于 trampoline 从内核链接基址开始建立的首个 2 MiB 映射内。当前产物
保持 `_start < relocate_enable_mmu < _start_kernel`；私有 trap helper 和 park 入口没有
相对顺序 ABI，只要求同样位于 `.head.text` 并被首个 2 MiB 映射覆盖。

这些独立入口函数的裸汇编是必要的 `unsafe` 边界。`relocate_enable_mmu` 的 `SAFETY`
契约必须覆盖 early root 的页对齐与来源、两套页表的发布状态、`satp_mode` 的有效性，以及
两套映射对当前 `.head.text` 的覆盖。寄存器约定、栈有效性和 PC-relative 符号访问也必须
由紧邻说明覆盖。固定 sibling
`../linux-6.12` 只用于核对 RISC-V 64 位机制和顺序；不得复制其 Linux image header、
汇编实现、最终页表或其他启动策略，也不得使 sibling 成为构建依赖。
