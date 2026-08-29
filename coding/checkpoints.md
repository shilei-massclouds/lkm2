# 生成式实现 Checkpoint 契约

Checkpoint 是 Model IR 中已建立函数栈后实际可达的实现观测点。通常只为每个 `ensures`
和 `invariant` 表达式生成 checkpoint；`establishes` 仍由模型推导器执行，不改变模型语义。
MemBlock mapping 显式把两个 provenance/completion `establishes` 纳入只读实现观测，因此其
11 条 invariant 与 2 条 establishes 合计 13 条均有 canonical ID；VM/Swapper 的既有集合不变。

## 身份、ABI 与完整性

每个声明点拥有独立的 canonical ID。ID 由具体对象、transition 或 state、块类型以及
规范化表达式构成；从类型继承的表达式必须先具体化到对象。链接符号由可读 slug 和该
ID 的 SHA-256 前 16 个小写十六进制字符组成。哈希不得包含源码行号、声明顺序或里程碑
顺序；不同 ID 得到相同截断哈希时生成必须失败。

Rust 声明必须使用 `extern "C"` 和 `u64` 参数，C 声明使用 `uint64_t`。每个 checkpoint
链接符号必须由所选 handler 明确定义；缺少定义必须成为链接错误。受版本控制的实现
映射必须与所有可达 checkpoint 一一对应，缺失、多余、重复里程碑或观测参数均为生成
错误，不得警告后跳过。

当前 VM 生命周期固定包含 28 项，执行顺序和六个里程碑由
`tools/checkpoints/vm.json` 唯一记录；M1 另有独立的 `SwapperPageTable` 9 项
`swapper_online` 套件，由 `tools/checkpoints/swapper.json` 记录。MemBlock 的 13 项由
`tools/checkpoints/memblock.json` 记录，分属四个独立 milestone。旧套件的对象状态等式没有参数；
其余表达式使用映射中冻结的参数名顺序。观测值必须来自实际 KernelMap、SATP 和页表表项；
二进制镜像大小、末叶子地址、页表页地址和 root PPN 不属于跨实现协议。

在上述 50 项之后还有独立的 `swapper-content` implementation-only 套件。它不从 Model IR
提取表达式，也不伪造 model invariant；三个固定 ID 分别观测 fixmap、linear map 和本侧
kernel image walker 自洽性。前两项参数顺序为 `valid,count,digest_lo,digest_hi`，kernel 项
仅为 `valid`。runner 对任何 `valid != 1` 立即失败，避免双方同时构造失败被误判为一致。

## Swapper 页表内容协议

统一语义项是按 VA 递增的 4 KiB `(va:u64, pa:u64, normalized_flags:u64)`，三个字段均按
小端编码。Sv39/Sv48/Sv57 walker 支持 1 GiB、2 MiB 和 4 KiB leaf；huge leaf 全部展开，
root、任意中间页表 backing PA、leaf level 与分配顺序均不进入摘要。摘要流以 `LKMPTE1`、
版本与 class 开头，以 count 结尾，并使用两个不同初始种子的流式 FNV-1a64 lane 形成
`digest_lo/digest_hi`。它只用于错误检测，不声明密码学安全性。

fixmap 摘要覆盖最终两 PMD 的 FDT 窗口并保留稳定的 `V/R/W/X/U/G/A/D` 位。linear 摘要覆盖
规范化 MemBlock Memory 中实际可进入 direct map 的完整范围；Linux `for_each_mem_range()`
排除的前导固件 `MEMBLOCK_NOMAP` 保留区（QEMU virt 上为 OpenSBI 所有）在 Rust 侧也从实际映射
和摘要域排除。其余范围保留绝对 VA/PA，flags 只投影为双方共同稳定的有效、可读语义，因此各自
text/rodata 别名的 W/X 分段差异不会污染比较。kernel 只检查本侧
`[kernel_va, kernel_va + image_size)` 无空洞、VA→PA 偏移正确、leaf 合法且不出现 W+X；它不
输出 count/digest，也不检查镜像尾部之后的映射。

内容不一致的诊断协议使用启动参数 `lkm2.ptdiag`。`LKMPTC1` 以 512 个规范项（2 MiB）为
chunk 定位首个差异，`LKMPTI1` 只输出目标 chunk 最多 512 项；runner 的解析和首项定位不会
在正常成功路径输出整个 128 MiB 页表。

## MemBlock 范围协议

只读观测固定在 `setup_bootmem` 成功返回后、`setup_vm_final` 首次动态分配页表页之前。Memory
和 Reserved 均先按 base 排序，再合并相邻或重叠的半开区间。相关 canonical checkpoint 携带
`<kind>_count` 与 `<kind>_digest`；digest 是对每个 `(base, end)` 按大端 u64 字节串依次执行的
64 位 FNV-1a。debugcon 同时发出 `LKMRNG1` 旁路记录，runner 校验索引连续、序列已规范化且
count/digest 闭环一致。

Memory 比较完整序列。Reserved 的跨实现序列排除各自 KernelImage 的具体物理长度，因为 Linux
与 lkm2 二进制大小天然不同，且该大小已明确不属于跨实现协议；KernelImage 已保留由独立
canonical checkpoint 覆盖。Linux 的 Reserved 投影是 `memblock.reserved` 与带
`MEMBLOCK_NOMAP` 的 memory region 之并集再减去 vmlinux 区间，从而保留 FDT reserve-map/
`reserved-memory` 的不可分配语义。lkm2 保留完整内部 Reserved，仅在 checkpoint 投影中减去
自身 KernelImage。

差分失败时 runner 输出完整双方规范化序列和首个不一致索引。该套件只读；回滚只需移除
MemBlock mapping、生成模块、四个调用点和增量 sibling patch，旧 37 项 ABI 与 ID 无需改变。

## VM 与 ArchHead 覆盖边界

VM checkpoint 的提取 scope 固定为 `objects.vm / Vm / arch_head_stack_established`，只描述
`setup_vm` 在 MMU-off 环境构造 KernelMap、trampoline 和 early 页表的语义。现有 28 项已经
覆盖 Vm 完成 Setup 后的 Ready 关系、三类页表 Ready invariant、early kernel image 映射和
early DTB 映射；这些事实不得通过追加 ArchHead checkpoint 重复表达。

ArchHead 的 `ensures` 位于上述 scope 之外，`establishes` 按生成规则不产生实现 checkpoint。
因此 CurrentTask、KernelImage/BSS、BootStack、interrupt mask/pending、虚拟 `gp`、FS/VS、
hart id、SATP 激活、trap context 和 SoC early init 等事实，只由 model derive、ArchHead coding
契约和实现测试检查。它们不得进入 VM canonical ID 集合，也不得改变六个里程碑、生成 ABI、
实现调用点或 Linux sibling 指纹。

若未来需要运行时观测 ArchHead 的最终硬件状态，必须新增独立的 ArchHead checkpoint 套件，
并单独设计 scope、实现映射、观测 ABI 和验收入口；不得扩展 `tools/checkpoints/vm.json`。

## 生成边界与 handler

独立的 `checkpointgen` 消费 Model IR 和受版本控制映射，生成 canonical manifest、Rust
声明/定义和 mapping 所列 milestone wrapper。普通 lkm2 构建只生成 Rust 产物，不读取 sibling。

`CHECKPOINT_HANDLER=empty|debugcon` 选择实现，默认 `empty`，未知值必须在构建开始时失败。
empty handler 与调用位于同一 Rust crate；优化构建不得保留 checkpoint 调用或任何记录
字符串。debugcon handler 不分配、不格式化、不记录日志且不 panic，直接执行原始 SBI
DBCN `CONSOLE_WRITE_BYTE` ecall 并逐字节写出：

```text
LKMCP1 id=<canonical-id> hash=<16hex> key=0x<16位小写十六进制>...
```

DBCN 错误不改变启动控制流。页表观测只能通过安全的 `AtomicU64` 读取和安全逐级 walk，
不得为 checkpoint 新增裸指针写入或无说明的 `unsafe`。

### PhaseTest

PhaseTest 是 lkm2 单侧的启动阶段对象测试，不属于 Linux differential 或普通 checkpoint
套件。构建期 `PHASE_TEST` 为空时不编译测试路径；当前唯一受支持的值为
`memblock-basic`，未知值必须在构建开始时失败。公开入口为
`make phase-test PHASE_TEST=memblock-basic`，默认 `make test` 也运行该测试。

测试在 `MemBlock.Online` 观测完成后接收唯一的 `&mut MemBlock`，完成实际物理范围分配、
释放和状态恢复，然后通过 SBI SRST 关闭虚拟机，不继续执行 `setup_vm_final()`。终端只允许
一条固定记录：

```text
LKMPT1 test=memblock-basic checkpoint=MemBlock.Online result=pass
LKMPT1 test=memblock-basic checkpoint=MemBlock.Online result=fail case=<stable-case-id>
```

## Sibling patch 与执行

sibling 固定为相邻 `linux-6.12` 的 `dev` 分支。VM 套件仍使用
`d0fef99b651d141dd6ffbbddeb8b729b2f8faaff` → `2f5f2bbdcdbede7b65b18f36cfcc72150a40ee0f`
锚点，从 patch-base Git 对象生成并校验四文件冻结 patch；M1 套件使用
`2f5f2bbdcdbede7b65b18f36cfcc72150a40ee0f` →
`33760df4d924f1d8f6b7c1e03c21bf03f0ac9d0b` 锚点，只增量追加 swapper include、handler
和 `pt_ops_set_late()` 后的观测调用，不改写旧 28 项协议。patch 是未跟踪构建产物；生成器
不得暂存、提交、切换提交或自动应用。显式应用必须先运行 `git apply --check`，通过
reverse-check 识别已应用或已集成状态。

MemBlock 套件使用 `33760df4d924f1d8f6b7c1e03c21bf03f0ac9d0b` →
`acb69c4c4d9a3eb63cab13eeaf47bf118b969ccb` 锚点，以三文件独立增量追加 include、handler 与
`setup_bootmem()` 后的四个调用；生成内容由三文件 SHA-256 锚定。

`swapper-content` 以该 MemBlock integrated commit 为 patch base，仅在同三个文件追加 content
include、handler 和 `pt_ops_set_late()` 后的观测调用，并冻结
`e5668acadb200fd194c988329288810338eba963` 为 integrated commit。runner 接受生成内容逐字一致
的未暂存 review patch，或该 integrated commit 上的干净工作树；干净 MemBlock 基线不视为已集成。

Linux patch 在 `setup_vm` 的同六个语义边界调用生成 wrapper，并以独立 C 文件提供固定
DBCN handler。它运行在 `sbi_init()` 之前，所以不得调用 Linux SBI 或 logging API，也
不增加 Kconfig 选择。MMU-off 调用和字符串访问都必须验证为可用的 PC-relative 访问。

差分 runner 固定使用同一 OpenSBI、QEMU virt、128 MiB、单 hart 和默认 CPU，过滤
`LKMCP1`。Sv57 必须严格比较 53 项 canonical ID 顺序、参数名顺序和所有值，并比较 Memory/
Reserved 的 `LKMRNG1` 序列；缺失、重复
或差异均失败。Sv48 通过 CPU 配置禁用 Sv57，Sv39 同时禁用 Sv57/Sv48；后二者只验证
lkm2 回退与记录自洽。

顶层 `make difftest` 是 Sv57 严格差分的正式入口。runner 对 VM 套件接受 patch base
上的未暂存 review patch或 VM integrated 提交上的干净工作树；对 M1 套件还接受
VM integrated 提交上的未暂存增量 patch，或 M1 integrated 提交上的干净工作树。review
状态都必须位于冻结分支、不得包含暂存修改；MemBlock review 或 integrated 状态均严格比较
旧 50 项；content review 或 integrated 状态严格比较全部 53 项记录。

缓冲 handler、Sv48/Sv39 sibling 差分、Linux alternatives/KASLR/
`pt_ops` 迁移不在本阶段范围；ArchHead 对已观测 early 页表的 SATP 重定位不增加新的
checkpoint 协议项。
