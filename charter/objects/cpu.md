# CPU 与 EventFlow 章程

## CPU 身份、线路与 current

`CPU` 以 `cpu_core: true` 标记；每个实例必须声明唯一、非负的 `logical_id`。
当前只物化 `BootCPU`（逻辑 CPU0），`Cpu0Scheduler` 以 `parent: BootCPU` 明确归属。
推导线路创建时即把 `CurrentCPU` 绑定为该 Scheduler 的 CPU owner。它是独立、只读的
线路 selector，不依赖 `CurrentTaskRef`，也不通过 TaskFlow 反向推断。因此启动早期
current Task 尚不可用时，CPU 身份仍必须可解析。

`CurrentTaskRef` 在线路创建时为 unavailable。只有 `BootTask` 在 `State::OnCpu`
声明的 `Action::ResetCurrent` 可以首次发布它；成功前必须确认 BootTask 的唯一
TaskFlow 已绑定 `CurrentCPU`。重复 Reset、错误对象、错误状态、CPU 未绑定或 handler
失败都不得改变 current。正常调度切换仍在完整事务成功后提交 next current，并先把
next TaskFlow 绑定到该线路的 `CurrentCPU`。

## per-CPU interrupt control

`CurrentCPU.InterruptControlRef` 是推导器提供的动态 selector，每个 CPU 独立拥有
`Unknown`、`Masked`、`Unmasked` 模式和 FIFO pending 集合：

- `MaskAll` 只切换到 `Masked`，不清除已经 pending 的输入；
- `ClearPending` 只丢弃当前集合，不改变模式；
- `Unmask` 切换到 `Unmasked`，并按输入到达顺序逐一投递 pending interrupt。

`Unknown` 模式收到 interrupt 必须失败。`Masked` 模式收到的输入已被消费到目标 CPU
的 pending 集合，但不物化 EventFlow。该 FIFO 是确定性推导协议，不声称复刻硬件中断
优先级。Exception 和 syscall 不受 interrupt mode 控制。

## 统一 EventFlow

`EventFlow` 是统一基类；`InterruptFlow`、`ExceptionFlow` 与 `SyscallExitFlow` 都以
`event_flow: true` 标记且不声明具名实例。CPU 分别由 `Action::OnInterrupt`、
`Action::OnException`、`Action::OnSyscallExit` 接收，并通过动态只读 selector
`self.InterruptFlowRef`、`self.ExceptionFlowRef`、`self.SyscallExitFlowRef` 进入 fresh
CPU-owned 实例。实例编号按 CPU、类型确定性递增；事件名称保留在推导结果元数据，首版
不向 model 暴露 cause 分派。

Interrupt 可以显式投递到任一已存在 CPU，受目标 CPU control gate 约束，处理后返回
原 TaskFlow/UserAppRuntime 坐标。Exception 绕过 mask，但只能投递到 Runtime 所属的
本地 CPU，抽象处理后同样返回。SyscallExit 也必须本地投递，并保持 terminal 语义，
最终驱动 `CurrentTaskRef.Action::Exit(status)`，不走恢复路径。

EventFlow 不调用 Scheduler、不驱动 Task Suspend，也不改变 runq 或 TaskFlow CPU
绑定。进入时建立实例级 `event_flow_handled(self)` fact；可返回 flow 仅在 handler
成功后恢复原坐标。任何 EventFlow 活跃期间再次进入 EventFlow 必须以
`nested_event_flow` 失败。

临时模型映射：[CPU](../../model/objects/cpu.spec)、
[EventFlow](../../model/flows/event_flow.spec)、
[SyscallExitFlow](../../model/flows/syscall_exit_flow.spec)。
