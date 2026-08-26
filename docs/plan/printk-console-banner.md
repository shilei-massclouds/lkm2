# Printk、Console 与 Banner 生命周期

## 当前冻结范围

- `Printk: PrintkType` 由 Kernel 持有，初始 Online，没有 transition、消息 Action 或独立
  buffer 对象；Online 只表示静态日志入口可接受记录。
- `Banner: BannerType` 由 Kernel 持有，初始 Ready，只支持
  `Enable: Ready → Online`。实例 handler 的唯一 ensure 是 `Printk.state == State::Online`。
- 正式模型使用通用 `ConsoleType`，`EarlyConsole` 是该类型的当前实例。工具关系测试中的
  独立 `EarlyConsoleType` fixture 不随正式模型改名。
- `EarlyConsole.Enable` 完成既有 backend 查询与 binding 后，同时建立
  `printk_console_registered(Printk, EarlyConsole)`；Online invariant 保持注册与 binding
  两个事实。

EarlyBoot 的固定顺序为：

```text
Banner.Enable
  -> DtbBlob.Enable
  -> EarlyConsole.Enable
  -> Cpu0Scheduler.Enable
  -> local IRQ Unmask
```

成功路径确保四个生命周期对象 Online，并只检查 `BootCommandLine` 存在 `earlycon` 键，
不把当前验收输入 `sbi` 提升为 EarlyBoot 契约。缺失 DTB bootargs 时，Banner 已 Online，
DtbBlob binding 失败，Console、Scheduler 和 Unmask 不执行。

## Coding 边界

Banner Enable 映射为通过 Printk 提交内核 banner，对应 Linux sibling 的
`pr_notice("%s", linux_banner)`，且不依赖 Console 已经 Online。实现关系固定为
`println! → Printk → registered Console → selected backend`，但本轮不修改 Rust 实现。

格式化、ring buffer、记录序号、flush/replay、锁与 SBI 调用属于 coding/impl；checkpoint
debugcon 保持独立。model 不新增 `Printk.Action::Emit`、
`EarlyConsole.Action::Write(message)` 或消息级 predicate。只有未来正式验证缓冲、丢弃、
replay 或多 Console cursor 时，才引入抽象 Emit/Flush 行为。
