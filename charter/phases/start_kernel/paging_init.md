# PagingInit 章程

PagingInit 对应 RISC-V `paging_init()` 的启动边界。它在 EarlyBoot 已经完成 DTB Memory、SBI
capability 与 EarlyConsole 后运行，固定顺序为：

```text
setup_bootmem:
  MemBlockReserved.Enable
  MemBlock.Enable
setup_vm_final:
  FinalPageTable.Enable
Cpu0Scheduler.Enable
InterruptControl.Unmask
```

`MemBlockReserved.Enable` 在 `setup_bootmem` 内纳入 KernelImage、DTB 文件自身、FDT reserve map
和静态 `/reserved-memory`；完成后父 `MemBlock.Enable` 只检查 Memory 与 Reserved 均 Online。
因此“加入 KernelImage 与 DTB 自身保留”是 `setup_bootmem` 的职责，不是 `DtbBlob.Enable` 或
`parse_dtb` 的副作用。

只有 MemBlock Online 后才能启用 `FinalPageTable`，表达 `setup_vm_final` 的内存前提。随后才启用
CPU0 Scheduler，并以当前 CPU 的 Unmask 作为最后一个 drive；Unmask 成功后建立供 BootSetup
消费的 `early_boot_interrupts_enabled()`。

reservation 失败时 Memory、SBI capability 与 EarlyConsole 保持 Online，Reserved、MemBlock
与 FinalPageTable 保持 Ready；Scheduler、Unmask 和 BootSetup 不执行。最终页表、Scheduler 或
Unmask 任一失败也不得建立交接事实。

模型映射：
[`model/phases/start_kernel/paging_init.spec`](../../../model/phases/start_kernel/paging_init.spec)。
