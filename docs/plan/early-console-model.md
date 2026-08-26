# EarlyConsole 逐步倒推计划

## 核心对象

正式模型固定以下四个核心对象：

- `BootCommandLine: Relation<String, String>`：内核已经观察到的启动命令行键值；
- `EarlyConTable: Map<String, EarlyConsoleBackendType>`：early console backend 注册表；
- `SbiConsole: EarlyConsoleBackendType`：SBI early console backend；
- `EarlyConsole: EarlyConsoleType`：根据命令行和注册表绑定 backend 的前端对象。

选择链固定为：

```text
BootCommandLine["earlycon"]
    -> "sbi"
    -> EarlyConTable["sbi"]
    -> SbiConsole
    -> EarlyConsole.state == State::Online
```

`EarlyConsole` 不静态引用 `SbiConsole`；具体 backend 只能来自有限关系查询。

## M1：输入已就绪时完成绑定

本里程碑只证明正式启动推导可以消费已经准备好的输入：

- `Kernel.Transition::Setup` 原子提交
  `BootCommandLine.contains("earlycon", "sbi")` 和
  `EarlyConTable.contains("sbi", SbiConsole)`；
- `SbiConsole` 作为已经编入内核且可用的 backend，初始即为 `State::Online`；
- `EarlyBoot.Action::Enter` 只启用 `EarlyConsole`，随后启用 scheduler 并打开中断；
- `EarlyConsole.Transition::Enable` 依次执行 `unique_value` 和 `lookup`，保存 backend，
  进入 `State::Online` 并建立绑定事实。

这里的 Kernel Setup 是 M1 粗粒度的“内核输入已形成”边界，不声称描述了字符串解析、
early-param 处理或链接表遍历。OpenSBI 只负责固件生命周期与 Kernel handoff，不建立
内核命令行或 earlycon 注册表 tuple。

完成标准：默认推导轨迹包含两个成功 binding，最终 tuple 快照包含命令行与注册表
条目，`EarlyConsole.backend == SbiConsole`，不存在 `SbiConsole.Transition::Enable` 推导
unit，且 `SbiConsole` 最终仍为 Online；原有启动推导继续通过。

## 后续里程碑

### M2：倒推 BootCommandLine 来源

确认 Linux sibling 中 `setup_arch` 的 bootargs/内建命令行合并边界，将命令行 tuple
的建立从当前 Kernel Setup 收紧到能够说明其真实来源的 model handler。字符扫描和
参数解析算法仍属于 coding。

### M3：倒推 EarlyConTable 来源

确认 earlycon 声明、early-param 处理和链接表的生成边界，将 `sbi -> SbiConsole` 的
建立从当前 Kernel Setup 收紧到对应的内核镜像或注册阶段。表遍历和 section 布局属于
coding/impl。

### M4：收紧 SbiConsole 可用性

把 `SbiConsole` 的初始 Online 事实细化为实际所需的 SBI console capability，区分
DBCN 与 SBI v0.1，并补齐缺失、歧义、未注册和 backend 不可用在正式启动链上的失败
验证。

每个里程碑独立保持可推导、可回归；后续工作替换上一阶段的抽象来源，不改变四个
核心对象和两次查询的选择语义。
