# ArchHead 章程

ArchHead 把固件入口到 `StartKernel` 的聚合交接拆成 CPU、KernelImage、CurrentTask、
BootStack 和 early address-space 五组可独立检查的契约。入口必须满足：OpenSBI handoff
已建立，`CurrentCPU == BootCPU` 且 CPU Online，BootTask 已 Resume 到 OnCpu，
KernelImage 为 Loaded，BootStack 和 Vm 均为 Base。此时 `CurrentTaskRef` 仍不可用。

## 固定驱动顺序

ArchHead 必须严格按以下顺序驱动，任何一步失败都不得伪造后续事实：

1. `CurrentCPU.InterruptControlRef.MaskAll`；
2. `CurrentCPU.InterruptControlRef.ClearPending`；
3. `KernelImage.Action::ClearBss`，令 Loaded → Ready 并建立 BSS cleared；
4. `BootTask.Action::ResetCurrent`，首次发布 `CurrentTaskRef == BootTask`；
5. `BootStack.Preset`，令 Base → Prepared，发布 MMU-off 物理栈；
6. `Vm.Preset → Vm.Setup`，令 Base → Prepared → Ready；
7. `BootStack.Setup`，令 Prepared → Ready，发布 early 虚拟 current-task stack。

KernelImage 是 Kernel-owned 对象；BootStack 是 BootTask-owned 对象。ResetCurrent 不清
Task 数据结构，也不初始化 stack，因此三者不得合并为一个隐式动作。FDT 暂不建立对象，
其身份和物理地址继续由 OpenSBI handoff 与编码契约保证。

## StartKernel 交接

ArchHead 完成时必须能分别观测并由 StartKernel 复查：

- CPU interrupt 已 masked，且入口 pending interrupt 已独立清除；
- 虚拟 `gp` 就绪，FPU/Vector disabled，boot hart id 已记录；
- `CurrentTaskRef == BootTask` 且 bootstrap ResetCurrent 已完成；
- KernelImage、BootStack 与 Vm 均为 Ready；
- early address space 已激活，CPU 可访问 KernelImage 和 firmware FDT；
- StartKernel-safe early fail-stop trap 已安装，SoC early init complete。该 trap 保证只用于
  早期失败停机，不表示 runtime interrupt dispatch 已就绪。

GP 的 MMU-off/虚拟两次加载、trampoline SATP、重定位和两阶段 stack 的指令顺序属于
coding 契约；model 只保留最终可交接事实。ArchHead 不改变 Rust ABI、checkpoint 调用
或 QEMU debug 协议。

临时模型映射：[model/phases/arch_head.spec](../../model/phases/arch_head.spec)

章程与模型的关系见[系统章程](../main.md)。
