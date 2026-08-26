# EarlyConsole 逐步倒推计划

## 核心对象

正式模型当前固定以下六个 console 选择对象：

- `DtbBlob: DtbBlobType`：Kernel 持有的 DTB 输入，Online 表示 bootargs 已被观察并复制；
- `ChosenBootArgs: Relation<String, String>`：`DtbBlob` 的子对象，表示
  `/chosen/bootargs`；
- `BootCommandLine: Relation<String, String>`：内核已经观察到的启动命令行键值；
- `EarlyConTable: Map<String, EarlyConsoleBackendType>`：`KernelImage` 持有的链接期 early
  console backend 注册表；
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
    -> EarlyConsole.Enable
    -> lookup EarlyConTable["sbi"]
    -> SbiConsole
    -> EarlyConsole.state == State::Online
    -> printk_console_registered(Printk, EarlyConsole)
```

`BootCommandLine` 对象静态存在且初始为空；`DtbBlob.Enable` 增加的是 tuple 内容，
不是动态创建命令行对象。`EarlyConsole` 不静态引用 `SbiConsole`；具体 backend 只能来自
有限关系查询。

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
  `BootCommandLine.contains("earlycon", value)`，并在源、目标 relation 都包含该值后
  进入 Online；
- `EarlyBoot.Action::Enter` 严格依次启用 `Banner`、`DtbBlob`、`EarlyConsole`、
  `Cpu0Scheduler`，最后执行 local IRQ Unmask；成功交接时四个对象均为 Online，且
  `BootCommandLine` 存在 `earlycon` 键；具体值属于 DTB 输入，不是 EarlyBoot 契约；
- 缺失 DTB bootargs 时，Banner 已为 Online，DtbBlob binding 以
  `relation_key_missing` 失败，不建立 `BootCommandLine` tuple，也不继续启用
  EarlyConsole、scheduler 或打开中断。

OpenSBI 只负责固件生命周期与 Kernel handoff，不建立上述三个 relation/map 的内容。
本轮把 DTB 输入直接展开为 DtbBlob 的生命周期 transition，不引入仅作包装的
`DtbProperties`、`SetupArch` 或 `parse_dtb` 具名阶段。DtbBlob Online 只表示 bootargs
已被内核观察并复制，不表示 DTB 所在内存已经失效。M2 的当前验收输入是
`earlycon=sbi`，但 EarlyBoot 只断言键存在；字符串扫描、命令行合并优先级、其他 DT
属性和 SBI capability 留给后续里程碑。

M2 完成标准是 Setup 产生 chosen bootargs 与 registry 两个 relation effect；Banner 的唯一
ensure 与 DtbBlob 的 binding、同值 relation effect 和两项 ensures 全部通过；默认轨迹包含
五步 EarlyBoot drive，最终保留三个 tuple、backend 字段、registry binding 和 Printk
Console 注册事实。

## M3：EarlyConTable 链接期注册生命周期（当前）

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

## 后续里程碑

### M4：收紧 SbiConsole 可用性

把 `SbiConsole` 的初始 Online 事实细化为实际所需的 SBI console capability，区分
DBCN 与 SBI v0.1，并补齐缺失、歧义、未注册和 backend 不可用在正式启动链上的失败
验证。

每个里程碑独立保持可推导、可回归；后续工作替换上一阶段的抽象来源，不改变
BootCommandLine、EarlyConTable、SbiConsole、EarlyConsole 及两次 backend 查询的选择
语义。
