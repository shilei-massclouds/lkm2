# EarlyConsole 逐步倒推计划

## 核心对象

正式模型当前固定以下七个 console 选择对象：

- `DtbBlob: DtbBlobType`：Kernel 持有的 DTB 输入，Online 表示 bootargs 已被观察并复制；
- `ChosenBootArgs: Relation<String, String>`：`DtbBlob` 的子对象，表示
  `/chosen/bootargs`；
- `BootCommandLine: Relation<String, String>`：内核已经观察到的启动命令行键值；
- `EarlyConTable: Map<String, EarlyConsoleBackendType>`：`KernelImage` 持有的链接期 early
  console backend 注册表；
- `SbiCapability: SbiCapabilityType`：Kernel 持有的 SBI 探测结果；
- `SbiConsole: EarlyConsoleBackendType`：SBI early console backend；
- `EarlyConsole: ConsoleType`：根据命令行和注册表绑定 backend 的前端对象。

此外，Kernel 持有初始 Online 的 `Printk: PrintkType` 和初始 Ready 的
`Banner: BannerType`。Banner Enable 只确保 Printk Online；EarlyConsole Enable 在 backend
binding 之后建立 `printk_console_registered(Printk, EarlyConsole)`。

选择链固定为：

```text
Kernel.Setup
    -> EarlyConTable.Link
    -> EarlyConTable["sbi"] = SbiConsole

ChosenBootArgs["earlycon"]
    -> DtbBlob.Enable
    -> BootCommandLine["earlycon"]
    -> "sbi"

SbiCapability.Enable
    -> sbi_dbcn_available(SbiCapability)

BootCommandLine["earlycon"]
    -> EarlyConsole.Enable
    -> lookup EarlyConTable["sbi"]
    -> SbiConsole
    -> SbiConsole.Enable
    -> consume SbiCapability availability
    -> sbi_console_uses_dbcn(SbiConsole)
    -> SbiConsole.state == State::Online
    -> EarlyConsole.state == State::Online
    -> printk_console_registered(Printk, EarlyConsole)
```

`BootCommandLine` 对象静态存在且初始为空；`DtbBlob.Enable` 增加的是 tuple 内容，
不是动态创建命令行对象。具体 backend 仍只能来自有限关系查询；正式表当前只含 SBI
backend，因此 `EarlyConsole.Enable` 在 binding 之后静态驱动 `SbiConsole.Enable`，不把
binding 变量作为动态 signal target。当前 EarlyBoot 的生产者—消费者顺序固定为 Banner →
DtbBlob → MemBlockMemory → SbiCapability → EarlyConsole（nested SbiConsole）；随后 PagingInit
执行 MemBlockReserved → MemBlock → FinalPageTable → Scheduler → Unmask。MemBlock 的独立设计边界见
[`memblock-model.md`](memblock-model.md)。

## M1：输入已就绪时完成绑定（已完成）

M1 证明正式启动推导可以消费已经准备好的输入：

- `Kernel.Transition::Setup` 原子提交
  `BootCommandLine.contains("earlycon", "sbi")` 和
  `EarlyConTable.contains("sbi", SbiConsole)`；
- `SbiConsole` 作为已经编入内核且可用的 backend，初始即为 `State::Online`；
- `EarlyBoot.Action::Enter` 只启用 `EarlyConsole`，随后启用 scheduler 并打开中断；
- `EarlyConsole.Transition::Enable` 依次执行 `unique_value` 和 `lookup`，保存 backend，
  进入 `State::Online` 并建立绑定事实。

这里的 Kernel Setup 是 M1 曾使用的粗粒度“内核输入已形成”边界。M2 已替换其中
`BootCommandLine` tuple 的来源；M1 的 backend 查询和绑定语义保持不变。

## M2：从 DTB 建立 BootCommandLine（已完成）

- M2 验收时，`Kernel.Transition::Setup` 原子建立
  `ChosenBootArgs.contains("earlycon", "sbi")` 和
  `EarlyConTable.contains("sbi", SbiConsole)`，不再建立 `BootCommandLine` tuple；M3 已继续
  把 registry tuple 的来源收紧到 `EarlyConTable.Link`；
- `DtbBlob` 属于 Kernel、初始为 Ready，`ChosenBootArgs` 是其子对象；
- `DtbBlob.Transition::Enable` 通过
  `ChosenBootArgs.unique_value("earlycon")` 得到同一个值，建立
  `BootCommandLine.contains("earlycon", value)`；Enable 还消费 QemuVirtPlatform 建立的
  DTB 物理范围大小至少为 1 且范围有效的事实，缺少任一事实都不能进入 Online；
- M2 当时的 `EarlyBoot.Action::Enter` 严格依次启用 `Banner`、`DtbBlob`、`EarlyConsole`、
  `Cpu0Scheduler`，最后执行 local IRQ Unmask；M4 已在 DtbBlob 与 EarlyConsole 之间加入
  `SbiCapability`，后续 MemBlock 里程碑又在 DtbBlob 与 SbiCapability 之间加入
  `MemBlockMemory`，并把 Reserved/父 MemBlock 移到 PagingInit；成功交接时相应对象均为
  Online，且
  `BootCommandLine` 存在 `earlycon` 键；具体值属于 DTB 输入，不是 EarlyBoot 契约；
- 缺失 DTB bootargs 时，Banner 已为 Online，DtbBlob binding 以
  `relation_key_missing` 失败，不建立 `BootCommandLine` tuple，也不继续启用
  EarlyConsole、scheduler 或打开中断。

OpenSBI 只负责固件生命周期与 Kernel handoff，不建立上述三个 relation/map 的内容。
本轮把 DTB 输入直接展开为 DtbBlob 的生命周期 transition，不引入仅作包装的
`DtbProperties`、`SetupArch` 或 `parse_dtb` 具名阶段。DtbBlob Online 只表示 bootargs
已被内核观察并复制，不表示 DTB 所在内存已经失效。M2 的当前验收输入是
`earlycon=sbi`，但 EarlyBoot 只断言键存在；字符串扫描、命令行合并优先级、其他 DT
属性以及 SBI capability 的原始探测对象在 M2 尚未进入 model；M4 只新增其提交后的有效
availability 抽象。

M2 完成标准是 Setup 产生 chosen bootargs 与 registry 两个 relation effect；Banner 的唯一
ensure 与 DtbBlob 的范围前置条件、binding 和同值 relation effect 全部通过；默认轨迹包含
当时的五步 EarlyBoot drive，最终保留三个 tuple、backend 字段、registry binding 和 Printk
Console 注册事实；当前 EarlyBoot 仍是五个直接 drive，但第三步已经明确为 MemBlockMemory，
Scheduler 与 Unmask 属于 PagingInit。

## M3：EarlyConTable 链接期注册生命周期（已完成）

- `EarlyConTable` 改为 `KernelImage` 子对象，初始为 Base，且只有唯一
  `Link: Base → Ready`；`KernelImage` 自身的初始 Loaded 与 ArchHead ClearBss 生命周期保持
  不变；
- Link 建立 `EarlyConTable.contains("sbi", SbiConsole)`，Ready invariant 持续要求该注册
  事实，表示固定构建配置下 earlycon 链接表已经形成；
- `Kernel.Transition::Setup` 驱动 `EarlyConTable.Link`，只直接建立
  `ChosenBootArgs.contains("earlycon", "sbi")`，并确保表为 Ready 且包含 SBI backend；
- 缺少 Link 注册 effect 时，Ready invariant 失败，Kernel Setup 不提交，后续 Kernel Enable
  与 EarlyBoot 都不执行；
- Linux sibling 映射冻结为
  `EARLYCON_DECLARE(sbi, early_sbi_setup)` 生成条目，链接脚本经 `__earlycon_table` section
  将其纳入 KernelImage；正式模型固定假设构建时启用
  `CONFIG_SERIAL_EARLYCON_RISCV_SBI`，但本轮不新增 Config 对象。

表遍历、section 起止地址、setup 函数指针和链接器布局属于 coding/impl，不进入 model。
正式模型仍只包含静态编入的 `sbi → SbiConsole`，不展开其他 earlycon backend。Rust
`impl/` 和独立 `tools/tests/fixtures/early_console` fixture 保持不变。

完成标准：EarlyConTable 的 owner、Base 初态、唯一 Link、Ready invariant 和 SBI tuple 均有
结构测试；Kernel Setup 的直接 relation effect 只剩 chosen bootargs，SBI 注册 effect 由嵌套
Link 单元产生；默认输出显示 Kernel Setup 驱动 Link；既有成功路径、缺失 DTB bootargs、
Printk 注册和 `BootCommandLine.has_key("earlycon")` 回归保持通过。

## M4：收紧 SbiConsole 可用性（已完成）

- 新增 Kernel-owned `SbiCapabilityType` 与 `SbiCapability`，初始为 Ready，且只有唯一
  `Enable: Ready → Online`；默认 Enable 建立
  `sbi_dbcn_available(SbiCapability)`。Online 只表示探测完成，允许没有 transport，也允许
  DBCN/v0.1 同时可用；
- 两个上游输入 predicate 是 `sbi_dbcn_available(SbiCapability)` 与
  `sbi_v01_console_available(SbiCapability)`；后者合并抽象 legacy SBI 和构建配置形成的有效
  fallback，不新增 Config 对象；
- 保留 `sbi_console_uses_dbcn(SbiConsole)` 与
  `sbi_console_uses_v01(SbiConsole)` 两个下游选择事实。`SbiConsole` 初始为 Ready，且只有
  唯一 `Enable: Ready → Online`；Enable depends_on 要求 SbiCapability Online 且至少一项
  availability 成立，默认只建立 DBCN selection；
- SbiConsole Online invariant 要求恰好选择一个 transport、选择必须有对应的上游依据，并且
  仅在 DBCN 不可用时允许 v0.1 fallback；SbiConsole 不得自行建立 availability；
- `EarlyConsole.Enable` 保持 BootCommandLine → EarlyConTable 的 binding 顺序，不再要求
  backend 在入口前 Online。binding 完成后静态驱动 `SbiConsole.Enable`，再确保查询所得
  backend Online，最后提交 backend 字段、registry binding 与 Printk Console 注册事实；
- 正式表只含 `sbi → SbiConsole`，因此静态 nested drive 不引入多 backend 分派。当前编译器
  只允许 Task、CPU、runtime 与 InterruptControl 动态 signal target，不使用 relation/map
  binding 变量作为动态 target；
- Kernel Setup 与 EarlyConTable Link 保持不变；M4 将 EarlyBoot 从五个直接 drive 增为六个，在
  DtbBlob 与 EarlyConsole 之间插入 SbiCapability，并冻结 Banner → DtbBlob →
  SbiCapability → EarlyConsole → Scheduler → Unmask。后续 MemBlock 里程碑在 DtbBlob 与
  SbiCapability 之间插入 MemBlockMemory，并把父 MemBlock、Scheduler 与 Unmask 收敛到
  PagingInit；成功路径最终确保 MemBlock 和 capability Online。

Coding 映射固定为 `SbiCapability.Enable ↔ sbi_init()`，由它完成 version/DBCN extension
probe；`SbiConsole.Enable ↔ early_sbi_setup()`，由它按 DBCN →
`CONFIG_RISCV_SBI_V01` → `-ENODEV` 选择。固定 sibling 默认
配置关闭 v0.1，因此默认成功轨迹固定为 DBCN；v0.1 只作为替代配置测试路径。SBI 原始
版本、extension probe 结果、Config 对象、函数指针、逐消息写入与运行时写错误不进入本轮
model。Rust `impl/`、推导工具与独立 `tools/tests/fixtures/early_console` relation fixture
保持不变。

失败验收覆盖：删除 capability availability effect 时，Capability 保持 Online，而
SbiConsole 以 `depends_on_failed` 停止；同时建立两个 selection 时互斥 invariant 失败；DBCN
仍可用却强选 v0.1 时 backing/优先级 invariant 失败。为同一 bootargs key 建立两个 value
仍得到 `relation_key_ambiguous`，选择未注册 key 仍得到 `map_key_missing`。所有 Console
失败都不提交 `EarlyConsole.backend`、registry binding、Printk 注册或 selection，不启用
Scheduler、Unmask IRQ 或 BootSetup；已经提交的 capability 状态与 availability 按前序事务
边界保留。

成功变体覆盖：同时把 availability 与 selection 替换为 v0.1，验证 fallback 注册成功；同时
建立 DBCN/v0.1 availability 时默认仍选择 DBCN 并成功。缺失 DTB bootargs、M3 Link failure、
`BootCommandLine.has_key("earlycon")` 与完整启动成功回归继续保留。

完成标准：默认轨迹证明 capability 的 Ready 初态、唯一 Enable 与 DBCN availability effect，
M4 当时的六个 EarlyBoot 直接 drive；当前阶段拆分后 EarlyBoot 为五个直接 drive，
PagingInit 另有五个 drive。SbiConsole 的上游
depends_on、DBCN selection 和 backing invariant，
以及最终同时保留 availability、selection、backend 字段和注册事实；上述成功/失败变体均保持
正确快照与 fail-stop 顺序，
`make derive`、`make test`、`make difftest` 和 `git diff --check` 全部通过。

## M5：EarlyConsole DBCN 运行时实现（已完成）

`DtbBlob` 按四层对应关系独立拆分，不再作为 EarlyConsole 的内部定义：model
[`model/objects/dtb_blob.spec`](../../model/objects/dtb_blob.spec)、charter
[`charter/objects/dtb_blob.md`](../../charter/objects/dtb_blob.md)、coding
[`coding/objects/dtb_blob.md`](../../coding/objects/dtb_blob.md) 和 implementation
[`impl/objects/dtb_blob.rs`](../../impl/objects/dtb_blob.rs)。EarlyConsole 层只消费 `DtbBlob`
产出的 `/chosen/bootargs`，并负责将其收窄为 `BootCommandLine` 以及查询链接期 backend
registry。

M5 不再改变上述正式模型的语义，而是把已经冻结的生产者—消费者链实现到 Rust：Banner 先进入
固定 Printk 缓冲，内核从 ArchHead 建立的只读 DTB fixmap 中校验并复制唯一
`/chosen/bootargs`，通过链接期 `EarlyConTable` 选择 `sbi` backend，探测 SBI v2.0 与 DBCN，
启用并注册 Console 后 FIFO 回放 `LKM2 kernel\n`，最后在中断仍屏蔽的状态停驻。

M5a 实现 DTB 输入、无外部 crate 的 FDT/bootargs 解析和唯一链接表查询；M5b 实现可注入的
SBI 调用边界、capability 与 DBCN backend；M5c 实现 Printk 缓冲、原子注册语义和 QEMU
smoke runner。M5 只实现当时 EarlyBoot 的可运行前缀，不实现 Scheduler、Unmask、BootSetup 或
SBI v0.1。正式模型保留 v0.1 替代路径，coding 文档明确其 Rust 实现仍未覆盖。

完成实现保持 `setup_vm` ABI、正式 model、relation fixture、28 项 VM checkpoint 与 sibling
patch 不变。宿主逻辑测试分别覆盖 FDT/命令行/registry、SBI capability/DBCN 参数与错误、
Printk FIFO/溢出/注册提交；默认 QEMU/OpenSBI smoke 已确认 `LKM2 kernel\n` 只出现一次并
回收进程。后续 MemBlock 实现里程碑已把运行前缀延伸到回放后的 `setup_bootmem`；它仍保持
中断屏蔽并停驻，没有越界实现 `setup_vm_final`、Scheduler、Unmask 或 BootSetup。

每个里程碑独立保持可推导、可回归；后续工作替换上一阶段的抽象来源，不改变
BootCommandLine、EarlyConTable、SbiConsole、EarlyConsole 及两次 backend 查询的选择
语义。
