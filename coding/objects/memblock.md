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
memory 裁剪、allocator API 与实际分配策略均未实现。
