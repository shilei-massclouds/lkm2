# PageAllocator 与 Buddy 编码约束

本页对应 [`model/objects/page_allocator.spec`](../../model/objects/page_allocator.spec) 和
`impl/objects/page_allocator.rs`、`impl/objects/zone.rs`。

## 所有权移交

`MemoryNode.Enable` 只建立 zone、FreeArea、MemMap 和 ZoneLists 的持久化后端；它不把任何
页交给 Buddy。`PageAllocator.Enable` 在这些对象全部 Online 后执行两阶段 handoff：

1. 通过 MemBlock 申请并记录 MemMap/page-state 与 Buddy block-record backing storage；这些
   物理区间继续属于 Reserved，不能进入可分配集合；
2. 重新计算各 zone 的 managed fragments，调用 `memblock_free_all` 等价路径，并将每个未保留
   页区间按最大对齐的 `2^order` 块种入对应 FreeArea。

MemBlock 保持 Online；`memblock_free_all_completed` 是“剩余页所有权已经移交”的独立事实。
handoff 完成后 MemBlock 分配和释放接口拒绝继续修改 Reserved，PageAllocator 才发布 Online。

MemMap 与 FreeArea 不拥有两份物理页：MemMap 提供每个 PFN 的页级描述/状态，FreeArea 只保存
空闲块头的 PFN 与 order 索引。也就是说，FreeArea 中的“页”仍然指向 MemMap 所描述的同一
物理页，并不是再复制一份 Page 数组。Linux sibling 的主 Buddy 路径也是把 `struct page` 的
`buddy_list` 挂入 `zone.free_area[order].free_list`；并没有一个独立的全局 Buddy bitmap。本实现
用无堆启动镜像中的有界 block descriptor 保存这组索引，同时仍通过 MemBlock 预留其物理
backing；该 descriptor 容器不改变上述所有权关系。Reserved、metadata、区间洞和 zone
envelope 外的地址永远不会被种入 Buddy。

某些 Linux 配置还会用 `pageblock_flags` bitmap 记录 pageblock 的迁移属性；它属于
pageblock/migration 元数据，不是 Buddy 空闲块的第二份索引，本轮不建模。

## order API

实现只提供连续、页对齐的阶数接口：

```text
AllocPages(order) -> Result<AllocatedBlock, BuddyAllocError>
FreePages(block, order)  -> Result<(), BuddyFreeError>
```

`AllocatedBlock` 固定携带 `start_pfn`、`order` 和所属 `ZoneKind`，同时提供物理起始地址、页数
和半开物理范围。分配从 `Movable -> Normal -> DMA32` 顺序回退，空 zone 跳过；高阶块按需
split，释放时检查 order、zone、边界、对齐和已分配记录，然后与空闲伙伴 coalesce。

非法 order、越界、未对齐、错误 zone、未分配块和重复释放都必须返回显式错误，不得静默接受。
Linux 的 GFP、migration type、watermark、reclaim、compaction、PCP 和任意页数申请不在本轮
实现范围内。
