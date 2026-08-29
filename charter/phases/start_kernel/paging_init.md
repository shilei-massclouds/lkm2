# EarlyBoot 页表与调度章程

仓库不建立显式 `PagingInit` phase，也不把后半段拆成独立 EarlyBoot actions。`StartKernel` 只
resume `EarlyBoot.Action::Enter`；该动作在完成 DTB Memory、SBI capability 与 EarlyConsole 后
继续按以下固定顺序展开：

```text
  MemBlockReserved.Enable
  MemBlock.Enable
  SwapperPageTable.Enable
  Cpu0Scheduler.Enable
  InterruptControl.Unmask
```

`MemBlockReserved.Enable` 在 EarlyBoot Enter 内纳入 KernelImage、DTB 文件自身、FDT reserve map
和静态 `/reserved-memory`；完成后父 `MemBlock.Enable` 只检查 Memory 与 Reserved 均 Online。
因此“加入 KernelImage 与 DTB 自身保留”是 Enter 后半段的职责，不是 `DtbBlob.Enable` 或
`parse_dtb` 的副作用。

只有 MemBlock Online 后才能启用 `SwapperPageTable`，表达 `setup_vm_final` 的内存前提。随后才启用
CPU0 Scheduler，并以当前 CPU 的 Unmask 作为最后一个 drive；Unmask 成功后建立供 BootSetup
消费的 `early_boot_interrupts_enabled()`。

reservation 失败时 Memory、SBI capability 与 EarlyConsole 保持 Online，Reserved、MemBlock
与 SwapperPageTable 保持 Ready；Scheduler、Unmask 和 BootSetup 不执行。最终页表、Scheduler 或
Unmask 任一失败也不得建立交接事实。

模型映射：
[`model/phases/start_kernel/early_boot.spec`](../../../model/phases/start_kernel/early_boot.spec)。
