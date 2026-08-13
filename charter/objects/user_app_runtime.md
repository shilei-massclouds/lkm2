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
`user_runtime` 标记的实例视为内核外黑盒：每个用户态执行 episode 默认触发一次普通
`Scheduler.Action::Schedule`，完全遵循 runq 策略。Task 被切走时，恢复点保留在
Runtime 入口；Task 再次被调度时回到同一用户态坐标，只确认该 episode 已恢复，不再
触发 Schedule，也不重启 TaskFlow 或从 `UserRunPhase` 的 yield 后方继续执行。这个
parked 坐标由 Task Resume 中 model-declared 的 `self.ResumeTargetRef` 解析得到；episode
结束后 selector 回退到 TaskFlow（当前尚无 Runtime exit，因此默认 episode 持续 parked）。未来的
syscall、中断或异常才会开启新的用户态 episode。

当前默认启动推导中，KernelInitTask Suspend 时将自己重新加入 runq，Scheduler 因而
再次选择 KernelInitTask，并完成 Resume 与 Dequeue。推导停在用户态黑盒边界，保留
BootHandoff 和 UserRunPhase continuation；StartKernel 不会恢复到 BootIdle，
`panic "boot idle repeated!"` 不可达。Runtime 在整个过程中保持 `State::Online`。

## trap 与 exit 边界

未来由推导器为该黑盒入口增加 syscall、异常和中断分支，并从用户态坐标进入对应
trap 入口。普通 syscall 可以在处理完成后返回同一用户态坐标；exit 将终止 Runtime，
并由异常或中断入口进入 Flow/Task 回收链。

当前模型不实现用户指令、syscall、exit、Runtime Disable/Cleanup 或 Task 回收。

临时模型映射：[model/objects/user_app_runtime.spec](../../model/objects/user_app_runtime.spec)

章程与模型的关系见[Objects 章程](../objects.md)。
