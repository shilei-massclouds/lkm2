# UserAppRuntime 章程

## 定位与 ownership

`UserAppRuntime` 是承载用户态执行坐标的运行对象，不是 TaskFlow 或 Scheduler
成员。类型以 `user_runtime: true` 标记；模型不声明具名 Runtime 实例，推导器在
完整复合选择器 `CurrentTaskRef.UserAppRuntimeRef` 首次求值时为当前 CPU 线路 Task
按需生成一个 owned child。因此 KernelInitTask 的运行实例身份是
`KernelInitTask.UserAppRuntime`，而不是全局的 `KernelInitUserAppRuntime`。裸
裸 `CurrentTaskRef` 可在任意 handler 中只读使用；`UserAppRuntimeRef` 是推导器虚拟
owned-child selector，不是模型字段，也不通过 Scheduler 或扫描 OnCpu Task 解析。

本阶段不为 Runtime 声明 `ApplicationInstance`。具体应用、exec 与地址空间等语义在
开始模拟应用执行时另行引入。

## 生命周期与用户入口

Runtime 的最小生命周期是：

```text
Base --Preset--> Prepared --Setup--> Ready --Enable--> Online
Online --Enter--> 用户态执行断点
```

`UserRunPhase.Action::Enter` 必须通过 `CurrentTaskRef.UserAppRuntimeRef` 同步依次
驱动 `Preset`、`Setup`、`Enable`，然后 `yields Action::Enter`。`Enable` 只发布
Online 状态；只有 `Action::Enter` 表示控制到达用户态执行坐标。

`Action::Enter` 在类型中是 abstract 入口，model 层不提供实现。推导器将带
`user_runtime` 标记的实例视为内核外黑盒，并在该入口消费实例私有 signal cursor 的
下一行。没有信号或程序耗尽时不调用 CPU 或 Scheduler，用户态坐标保持 parked，路径
结果为 `yielded`。可返回 EventFlow 完成后仍恢复同一 Runtime 坐标；terminal exit
不恢复。

当前默认内存程序是 `syscall.exit <local> 0`。KernelInitTask 在进入用户态前已经是
`OnCpu` 且仍在 runq；exit EventFlow 不执行 Task Suspend/Resume 或 Schedule，因而
这两个事实保持不变。PID 1 路径最终 panic，Runtime 仍保持 `State::Online`。

## trap 与 exit 边界

输入信号分为并列的 `interrupt.*`、`exception.*` 与 `syscall.*`。三类信号先路由到
目标 CPU，再分别创建 owned `InterruptFlow<N>`、`ExceptionFlow<N>` 或
`SyscallExitFlow<N>`。事件名称保留在结果元数据，首版不向 model 暴露 cause 分派。

Interrupt 受目标 CPU control gate 约束，可跨 CPU 指定目标；Masked 时进入该 CPU 的
pending FIFO，Unmask 时依输入顺序投递。Exception 绕过 mask，但必须投递到 Runtime
所属 TaskFlow `cpu_ref` 指向的本地 CPU。两者处理后恢复相同 Runtime 坐标。
`syscall.exit(status)` 同样仅限本地 CPU，最终进入
`CurrentTaskRef.Action::Exit(status)`，保持 terminal 语义。

当前不实现用户指令、普通 syscall 返回、Runtime Disable/Cleanup 或 Task 回收。

临时模型映射：[model/objects/user_app_runtime.spec](../../model/objects/user_app_runtime.spec)

章程与模型的关系见[Objects 章程](../objects.md)。
