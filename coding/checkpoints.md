# 生成式实现 Checkpoint 契约

Checkpoint 是 Model IR 中已建立函数栈后实际可达的实现观测点。本阶段只为每个
`ensures` 和 `invariant` 表达式生成 checkpoint；`establishes` 仍由模型推导器执行，
不生成实现调用，也不改变模型语义。

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
`swapper_online` 套件，由 `tools/checkpoints/swapper.json` 记录。对象状态等式没有参数；
其余表达式使用映射中冻结的参数名顺序。观测值必须来自实际 KernelMap、SATP 和页表表项；
二进制镜像大小、末叶子地址、页表页地址和 root PPN 不属于跨实现协议。

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
声明/定义和六个 wrapper。普通 lkm2 构建只生成 Rust 产物，不读取 sibling。

`CHECKPOINT_HANDLER=empty|debugcon` 选择实现，默认 `empty`，未知值必须在构建开始时失败。
empty handler 与调用位于同一 Rust crate；优化构建不得保留 checkpoint 调用或任何记录
字符串。debugcon handler 不分配、不格式化、不记录日志且不 panic，直接执行原始 SBI
DBCN `CONSOLE_WRITE_BYTE` ecall 并逐字节写出：

```text
LKMCP1 id=<canonical-id> hash=<16hex> key=0x<16位小写十六进制>...
```

DBCN 错误不改变启动控制流。页表观测只能通过安全的 `AtomicU64` 读取和安全逐级 walk，
不得为 checkpoint 新增裸指针写入或无说明的 `unsafe`。

## Sibling patch 与执行

sibling 固定为相邻 `linux-6.12` 的 `dev` 分支。VM 套件仍使用
`d0fef99b651d141dd6ffbbddeb8b729b2f8faaff` → `2f5f2bbdcdbede7b65b18f36cfcc72150a40ee0f`
锚点，从 patch-base Git 对象生成并校验四文件冻结 patch；M1 套件使用
`2f5f2bbdcdbede7b65b18f36cfcc72150a40ee0f` →
`33760df4d924f1d8f6b7c1e03c21bf03f0ac9d0b` 锚点，只增量追加 swapper include、handler
和 `pt_ops_set_late()` 后的观测调用，不改写旧 28 项协议。patch 是未跟踪构建产物；生成器
不得暂存、提交、切换提交或自动应用。显式应用必须先运行 `git apply --check`，通过
reverse-check 识别已应用或已集成状态。

Linux patch 在 `setup_vm` 的同六个语义边界调用生成 wrapper，并以独立 C 文件提供固定
DBCN handler。它运行在 `sbi_init()` 之前，所以不得调用 Linux SBI 或 logging API，也
不增加 Kconfig 选择。MMU-off 调用和字符串访问都必须验证为可用的 PC-relative 访问。

差分 runner 固定使用同一 OpenSBI、QEMU virt、128 MiB、单 hart 和默认 CPU，过滤
`LKMCP1`。Sv57 必须严格比较 28 项 canonical ID 顺序、参数名顺序和所有值；缺失、重复
或差异均失败。Sv48 通过 CPU 配置禁用 Sv57，Sv39 同时禁用 Sv57/Sv48；后二者只验证
lkm2 回退与记录自洽。

顶层 `make difftest` 是 Sv57 严格差分的正式入口。runner 对 VM 套件接受 patch base
上的未暂存 review patch或 VM integrated 提交上的干净工作树；对 M1 套件还接受
VM integrated 提交上的未暂存增量 patch，或 M1 integrated 提交上的干净工作树。review
状态都必须位于冻结分支、不得包含暂存修改；M1 状态严格比较全部 37 项记录。

缓冲 handler、最终内核页表、Sv48/Sv39 sibling 差分、Linux alternatives/KASLR/
`pt_ops` 迁移不在本阶段范围；ArchHead 对已观测 early 页表的 SATP 重定位不增加新的
checkpoint 协议项。
