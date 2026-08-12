# UserAppRuntime 章程

## 定位与 ownership

`UserAppRuntime` 是承载用户态执行坐标的运行对象，不是 TaskFlow 或 Scheduler
成员。类型以 `user_runtime: true` 标记；模型不声明具名 Runtime 实例，推导器在
`CurrentUserAppRuntimeRef` 首次求值时为当前 Task 按需生成一个 owned child。因此
KernelInitTask 的运行实例身份是 `KernelInitTask.UserAppRuntime`，而不是全局的
`KernelInitUserAppRuntime`。

本阶段不为 Runtime 声明 `ApplicationInstance`。具体应用、exec 与地址空间等语义在
开始模拟应用执行时另行引入。

## 生命周期与用户入口

Runtime 的最小生命周期是：

```text
Base --Preset--> Prepared --Setup--> Ready --Enable--> Online
Online --Enter--> 用户态执行断点
```

`UserRunPhase.Action::Enter` 必须通过 `CurrentUserAppRuntimeRef` 同步依次驱动
`Preset`、`Setup`、`Enable`，然后 `yields Action::Enter`。`Enable` 只发布 Online
状态；只有 `Action::Enter` 表示控制到达用户态执行坐标。

`Action::Enter` 在类型中是 abstract 入口，model 层不提供实现。推导器将带
`user_runtime` 标记的实例视为内核外黑盒：默认场景触发一次
`Scheduler.Action::Schedule`。Task 被切走时，恢复点保留在 Runtime 入口；Task 再次
被调度时重新进入同一用户态坐标，并由默认场景再次触发调度，而不是重启 TaskFlow
或从 `UserRunPhase` 的 yield 后方继续执行。

当前默认启动推导因此从 KernelInitTask 用户态切回 BootTask，使 StartKernel 进入
BootIdle；BootIdle 再调度 KernelInitTask，而 Runtime 的下一次默认调度又切回
BootTask，最终执行 `panic "boot idle repeated!"`。Runtime 在整个过程中保持
`State::Online`。

## trap 与 exit 边界

未来由推导器为该黑盒入口增加 syscall、异常和中断分支，并从用户态坐标进入对应
trap 入口。普通 syscall 可以在处理完成后返回同一用户态坐标；exit 将终止 Runtime，
并由异常或中断入口进入 Flow/Task 回收链。

当前模型不实现用户指令、syscall、exit、Runtime Disable/Cleanup 或 Task 回收。

临时模型映射：[model/objects/user_app_runtime.spec](../../model/objects/user_app_runtime.spec)

章程与模型的关系见[Objects 章程](../objects.md)。
