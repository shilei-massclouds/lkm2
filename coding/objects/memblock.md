# MemBlock 编码约束

本页对应 [`model/objects/memblock.spec`](../../model/objects/memblock.spec) 与
[`charter/objects/memblock.md`](../../charter/objects/memblock.md)。实现映射为
[`impl/objects/memblock.rs`](../../impl/objects/memblock.rs)。

## Memory 与 Reserved

`MemBlockMemory::derive_from_dtb` 在 SBI probe 前扫描根节点的有效 `device_type = "memory"`
节点，按根节点 `#address-cells/#size-cells` 解码非空 `reg`。至少一个有效、非零且不溢出的范围
才能提交 Memory；缺失或畸形输入必须整体失败。

`MemBlock::setup_bootmem` 消费已经提交的 Memory，并在同一返回边界构造 Reserved 和父
MemBlock。Reserved 必须纳入：

1. VM 在 `KernelMap` preset 时发布的 KernelImage 物理起点和大小；
2. FDT reserve map 的所有非终止条目；
3. 静态 `/reserved-memory` 子节点的 `reg`；
4. DTB 自身的物理起点和 FDT total size。

任一来源缺失、畸形、溢出或超过容量时不得返回父 MemBlock。已构造的
`MemBlockMemory` 仍是独立值；失败不代表其发现结果被回滚。

Memory 与 Reserved 都提供不泄漏 backing array 的只读半开区间迭代器。checkpoint 在
`setup_bootmem` 返回边界抓取 count/digest，并在任何页表页分配前输出完整区间旁路记录。
digest 使用大端 `(base, end)` 字节串上的 64 位 FNV-1a。

内部 Reserved 始终包含 KernelImage。跨实现 checkpoint 投影会减去 KernelImage 区间：不同
实现的镜像大小不是既有 checkpoint ABI 的比较项，而 `KernelImage.Ready` canonical checkpoint
仍验证该来源已完成。Linux 对等投影还把 `MEMBLOCK_NOMAP` memory region 纳入 Reserved，避免
丢失固件 FDT reserve-map 的不可分配语义。

## 容器和暂不支持项

当前实现不分配堆内存：Memory 最多保存 16 个范围，Reserved 最多保存 64 个范围；插入时按
物理地址排序，并合并重叠或相邻范围。半开区间以 checked addition 建立，零长度被拒绝。
这些是当前实现合同，不是 model 中的字段或区间语义。

只支持 1 或 2 个 address/size cells。动态 `/reserved-memory` 的 `size` 请求需要早期分配策略，
当前以 `DynamicReservationUnsupported` fail-stop；NOMAP、NUMA、hotplug、memory limit、usable
memory 裁剪和更高层 allocator 策略均未实现。实现提供
`allocate_phys(size, alignment)`（高地址优先、连续且避开并写入 Reserved）以及
`free_phys(base, size)`（支持裁剪、拆分和跨 reservation 释放）；这两个 API 属于实现级
能力，不写入 Model IR，也不改变现有差分 checkpoint ABI。

## PageAllocator handoff

`PageAllocator.Enable` 在调用 `memblock_free_all` 前，必须通过同一分配路径预留并记录
MemMap/page-state backing 与 Buddy block-record backing。两类区间均继续留在 Reserved 投影中，
并可由 `metadata_ranges()` 按 `MetadataKind` 观测。`memblock_free_all_completed` 是独立的
handoff 事实；MemBlock 保持 Online，但 handoff 后 `allocate_phys`/`free_phys` 拒绝改变其
Reserved 集合。未保留且落在 node/zone envelope 内的完整页由 PageAllocator 依据最大对齐块
种入 FreeArea，metadata、洞和 envelope 外地址不会被释放。

## `memblock_dump_all` 观测

Linux sibling 在 `misc_mem_init()` 的 `zone_sizes_init()` 之后调用 `memblock_dump_all()`。本实现
用 model 的 `MemBlock.Action::DumpAll` 只冻结这个调用顺序；model 不描述日志消息、格式或输出
通道。coding/impl 必须在 MemoryNode 后、PageAllocator handoff 前执行该边界，并沿用 sibling 的
`memblock=debug` 条件：输出 Memory/Reserved 的总字节数、范围数量及每个规范化半开区间；输出
失败必须沿早期启动的 fail-stop 规则处理。内部区间仍是半开区间，日志末端按 sibling 的
`base + size - 1` 形式展示。该诊断不改变 MemBlock 状态、Reserved 集合或任何 checkpoint ABI。
