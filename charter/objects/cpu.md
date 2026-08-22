# CPU 与 EventFlow 章程

## CPU 身份与 current

`CPU` 以 `cpu_core: true` 标记；每个实例必须声明唯一、非负的 `logical_id`。
当前只物化 `BootCPU`（逻辑 CPU0），`Cpu0Scheduler` 以 `parent: BootCPU` 明确归属。
`CurrentCPU` 不是对象实例，而是推导线路只读 selector：推导器从当前 Task 的唯一
TaskFlow 读取 mutable `cpu_ref`。未绑定或指向非 CPU 时必须返回
`invalid_current_cpu_ref`。

`BootInitFlow.Action::Enter` 在进入 ArchHead 前把 `cpu_ref` 绑定为 `BootCPU`；每次
Scheduler 成功提交 next Task 后，推导器在执行其延迟 ResumeTarget 前把 next TaskFlow
绑定到同一 CPU。该绑定不改变 Task 状态或 runq membership。

## 运行时信号接收

运行时输入的 `interrupt.*`、`exception.*` 与 `syscall.*` 在 CPU 接收边界并列。
当前唯一执行协议是：

```text
UserAppRuntime
  -> target CPU.Action::OnSyscallExit(status)
  -> suspend source TaskFlow 用户态坐标
  -> materialize CPU.SyscallExitFlow<N>
  -> SyscallExitFlow.Action::Enter(status)
  -> CurrentTaskRef.Action::Exit(status)
```

`syscall.*` 只能发往 Runtime 的本地 CPU。`<local>` 在消费时从 TaskFlow `cpu_ref`
解析；显式逻辑编号必须存在且等于本地 CPU。

## SyscallExitFlow

`SyscallExitFlow` 以 `continuation: true` 和 `syscall_exit_flow: true` 标记，不声明
具名实例。推导器为每次 exit 创建以目标 CPU 为 parent 的 fresh 实例，编号从 0
确定性递增，例如 `BootCPU.SyscallExitFlow0`。CPU handler 通过只读动态 selector
`self.SyscallExitFlowRef` 进入当前实例。

EventFlow 执行不调用 Scheduler，不驱动 Task Suspend，也不改变 runq。框架记录
被暂停的 TaskFlow、UserAppRuntime 坐标、flow enter 以及 returned/terminal 结果；
可返回 EventFlow 完成后保留原坐标供恢复。`syscall.exit` 是 terminal，不走恢复路径。

临时模型映射：[CPU](../../model/objects/cpu.spec)、
[SyscallExitFlow](../../model/flows/syscall_exit_flow.spec)。
