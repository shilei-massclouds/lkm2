# Vm 编码约束

本页对应 [`model/objects/vm.spec`](../../model/objects/vm.spec)，实现位于
[`impl/objects/vm.rs`](../../impl/objects/vm.rs)。本契约只覆盖 MMU-off 的早期
KernelMap、分页模式探测、trampoline 页表和 early 页表构造；不永久启用 MMU，也不
构造最终内核页表。

## ABI 与生命周期

实现必须继续导出且只导出以下 C ABI 入口：

```rust
extern "C" fn setup_vm(dtb_pa: usize)
```

导出名必须保持为 `setup_vm`。`VM`、KernelMap、两个页表及其辅助符号均为 Rust 私有，
不要求使用 Linux 全局符号名。`setup_vm` 必须按模型顺序同步执行：

```text
VM.preset(dtb_pa)
  -> 准备 KernelMap 与两个静态页表对象
  -> 使用 EarlyPageTable 的临时页表页探测分页模式
  -> 将唯一探测结果记录到 KernelMap
  -> 清理临时表项并恢复 SATP Bare

VM.setup(dtb_pa)
  -> 构造 TrampolinePageTable
  -> 构造 EarlyPageTable 的内核镜像和 DTB 映射
  -> 保持 SATP Bare
```

成功返回时，KernelMap 和两个页表对象均已 Ready，且读取 `satp` 必须得到 `0`。失败
路径不得返回。

## 对象与所有权

`VM` 是一个静态聚合对象，至少包含：

- 一个 `KernelMapType` 实例；
- 一个 `TrampolinePageTable: PageTableType` 实例；
- 一个 `EarlyPageTable: PageTableType` 实例，fixmap 分支包含在该实例内部；
- early DTB 的物理地址和虚拟地址；
- 一个静态 VM setup 错误码。

聚合对象必须声明为普通 `static`，不得使用 `static mut`。KernelMap 字段、DTB 字段、
模式状态和错误码等需要在早期入口写入的标量必须使用适当宽度的原子整数提供内部
可变性；安全方法负责在整数表示与 `PagingMode`、地址和错误码之间检查转换。

`KernelMapType` 必须记录：

- 唯一的 `PagingMode::{Sv57, Sv48, Sv39}`；
- 与该模式对应的层数、顶层位移和 `PAGE_OFFSET`；
- 内核链接虚拟地址、运行时物理地址、镜像大小及虚实偏移。

`PagingMode` 是分页层级、地址布局和 SATP MODE 编码的唯一事实来源。SATP MODE 位必须
由它按需计算；不得另设 `pgtable_l4_enabled`、`pgtable_l5_enabled`、独立
`satp_mode` 或其他可能与它不一致的状态。

模式探测由 `VM.preset` 协调，探测结果归 KernelMap。EarlyPageTable 只拥有探测所需的
临时页表存储；探测成功或失败后都必须恢复这些页为全零。使用临时存储不会使
EarlyPageTable 进入 Ready，它只有在最终内核镜像和 DTB 映射建立后才 Ready。

## 静态页表存储与安全接口

页表页必须等价于以下布局：

```rust
#[repr(C, align(4096))]
struct PageTablePage([AtomicU64; 512]);
```

不得使用堆分配、裸指针写表、可变全局引用或把页表存储伪装成普通 `u64` 数组。所有
读取、清零和写入必须经过 `PageTablePage` 的安全方法；索引必须先检查范围，禁止依赖
数组越界 panic。PTE 构造必须使用检查物理地址、PPN 范围、叶子对齐和权限组合的安全
接口。

最大 Sv57 布局采用固定、具名的页表页，不使用 Linux `pt_ops`：

- TrampolinePageTable：root、p4d、pud、pmd，共 4 页；
- EarlyPageTable：root，kernel 分支的 p4d/pud/pmd，以及 fixmap 分支的
  p4d/pud/pmd，共 7 页。

Sv48 和 Sv39 折叠不用的层级；对应页必须保持为零。未使用的表项同样必须保持为零。
中间表项只能包含物理 PPN 和 `V` 位，`R/W/X` 必须全为零；叶子表项不得设置 `U`。

早期启动只有 boot hart 执行 setup，但页表表项仍使用 `AtomicU64` 提供静态内部可变性。
普通构表可以使用明确的原子顺序；在 SATP 探测和 setup 返回边界必须通过 RISC-V
屏障保证页表写入对硬件可见，不能把 Rust 原子顺序当作 `sfence.vma` 的替代品。

## 分页模式与虚拟布局

模式及布局必须与固定 sibling 的 RISC-V 64 位、4 KiB 页配置逐项一致：

| 模式 | 层数 | SATP MODE | 顶层位移 | `PAGE_OFFSET` | DTB fixmap 起始 VA |
| --- | ---: | ---: | ---: | ---: | ---: |
| Sv57 | 5 | `10 << 60` | 48 | `0xff60000000000000` | `0xff1bfffffec00000` |
| Sv48 | 4 | `9 << 60` | 39 | `0xffffaf8000000000` | `0xffff8d7ffec00000` |
| Sv39 | 3 | `8 << 60` | 30 | `0xffffffd600000000` | `0xffffffc4fec00000` |

所有模式共用 `KERNEL_LINK_ADDR = 0xffffffff80000000`、4 KiB 基础页和 2 MiB PMD
叶子。DTB fixmap 地址来自 sibling 对应模式的 VMALLOC、VMEMMAP、PCI I/O 和 FIX_FDT
布局；实现应把上表作为本仓库常量，不得在构建时读取 sibling。

## Sv57 → Sv48 → Sv39 探测

`VM.preset` 必须按 Sv57、Sv48、Sv39 顺序尝试，不能用编译期布尔变量替代运行时探测。
每个候选模式都执行完整且相互隔离的一次探测：

1. 以探测函数所在 PMD 为起点，在 EarlyPageTable 的临时分支中建立两个连续的 2 MiB
   恒等映射，覆盖探测代码可能跨越的 PMD 边界；叶子权限为内核 R/W/X。
2. 检查两个 PMD 是否落在固定临时容量可以表达的同一条分支内；否则记录容量错误并
   fail-stop。
3. 在写 SATP 前执行所需内存屏障和全局 `sfence.vma`。
4. 写入候选模式与临时 root 页 PPN 组成的 SATP 值，再读取 SATP。
5. 无论读取结果是否匹配，都立即把 SATP 写回 `0`，再次执行全局 `sfence.vma`，并将
   所有临时表项清零。
6. 只有读取值与写入值完全相同才选择该模式；否则继续下一级。

Sv39 也不受支持时记录 `UnsupportedPagingMode` 并 fail-stop。探测完成后 KernelMap
记录所选模式及其派生布局，且 `satp == 0`；最终页表构造不得重新探测或维护模式副本。

## 最终早期映射

所有最终叶子均为 2 MiB PMD 映射。PTE 位遵循 RISC-V 定义：`V/R/W/X/G/A/D`
分别为 bit 0/1/2/3/5/6/7。

TrampolinePageTable 必须建立：

- VA `[kernel_virt, kernel_virt + 2 MiB)` 到
  PA `[kernel_phys, kernel_phys + 2 MiB)` 的映射；
- 叶子权限为 `V | R | W | X | G | A | D`；
- 除该首段路径外不建立其他叶子。

EarlyPageTable 的 kernel 分支必须建立：

- 从 `kernel_virt` 开始，以 2 MiB 向上覆盖 `_end - _start` 的完整镜像；
- PA 从 `kernel_phys` 开始等距递增；
- 每个叶子权限为 `V | R | W | X | G | A | D`；
- 映射范围必须处于固定 kernel 分支容量内，不能跨出其单个 PMD 页可表示的范围。

EarlyPageTable 的 fixmap 分支必须建立：

- 将 `align_down(dtb_pa, 2 MiB)` 开始的连续 4 MiB 映射到所选模式的 DTB fixmap 起始
  VA，共恰好两个 PMD 叶子；
- 叶子权限为 `V | R | W | G | A | D`，不得设置 `X` 或 `U`；
- `early_dtb_va = fix_fdt_va + (dtb_pa & (2 MiB - 1))`，同时保存原始
  `dtb_pa`。

内核物理地址和虚拟地址必须为 2 MiB 对齐；镜像大小取 `_end - _start`，所有加法、
向上取整、PPN 编码和 DTB 4 MiB 终点都必须检查溢出。

## 错误与 fail-stop

VM 保存稳定的非格式化错误码：

| 数值 | 名称 | 含义 |
| ---: | --- | --- |
| 0 | `None` | 尚无错误 |
| 1 | `UnsupportedPagingMode` | Sv57、Sv48、Sv39 均不可用 |
| 2 | `InvalidAlignment` | 内核地址或页表页不满足所需对齐 |
| 3 | `AddressOverflow` | 地址、镜像范围或 PPN 计算溢出 |
| 4 | `PageTableCapacityExceeded` | 固定页表页无法表达所需范围 |
| 5 | `InvalidMappingRange` | 映射范围重叠、越界或权限组合非法 |

错误码写入后进入永久 fail-stop。失败处理不得调用 `panic!`、断言、日志、串口、格式化
或分配；也不得用这些机制处理本可显式检查的错误。

## MMU-off 与 `unsafe` 边界

`setup_vm` 及其完整调用链在 MMU 关闭时运行，必须采用 medany/PC-relative 的符号访问；
不得生成只有启用内核虚拟映射后才可访问的绝对地址。实现完成后必须检查 ELF 重定位和
反汇编，而不能只依赖源码判断。

除以下硬件或链接边界外，VM 实现不得使用 `unsafe`：

- SATP CSR 的读、写或 swap；
- RISC-V `fence`、`sfence.vma` 和 fail-stop 指令边界；
- 读取 `_start`、`_end` 等链接符号地址所必需的最小边界。

每处必要的 `unsafe` 都必须紧邻具体 `SAFETY` 注释，说明当前执行模式、地址有效性、
别名条件及屏障责任。页表索引、PTE 构造、地址运算和对象聚合本身必须保持安全 Rust。

## Sibling 边界

`../linux-6.12` 仅用于确认机制、顺序、布局和权限语义。它不是构建依赖，Makefile 和
源码不得读取或链接 sibling 内容。实现不得复制 Linux 的 `pt_ops`、alternatives、
KASLR、日志、最终页表或其分散的分页模式全局变量；本阶段也不实现差分框架、
arch_head trace 或永久 MMU 切换。
