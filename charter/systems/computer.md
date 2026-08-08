# Computer 章程

计算机系统Computer是由硬件、固件、内核和应用集成形成的顶层系统。

Computer接收两类信号源发出的信号，人类Human和受控系统Plant。

## 章程模型

### 标准生命周期信号响应

1. OnPreset：引入/定义规格，形成设计。

   向Computer的各层构成部分发出Setup信号，驱动它们完成规格确认，建立已经完成规格和设计的事实。

   1. 向QemuVirtPlatform发出Preset信号，要求确认符合Riscv ISA规范；
   2. 向OpenSBI发出Preset信号，要求确认遵循SBI 规范；
   3. 向Kernel发出Preset信号，要求确认配置和LDS参照Linux Kernel v6.12；
   4. 向RootFs发出Preset信号，要求确认符合应用系统和测试系统清单要求。

   确认并基于上述四层的事实结果，建立Computer已经完成规格和设计的事实。

2. OnSetup：构造硬件平台、固件、内核和应用，并集成。

   向Computer的各层构成部分发出Setup信号，驱动它们完成构造过程并确认，建立已经完成构造的事实。

   1. 向QemuVirtPlatform发出Setup信号，要求建立硬件平台并确认存在；
   2. 向OpenSBI发出Setup信号，要求构造该固件并确认构造成功；
   3. 向Kernel发出Setup信号，要求构造目标内核映像文件并确认成功；
   4. 向RootFs发出Setup信号，要求构建应用系统、测试系统并部署形成根文件系统。

   确认并基于上述四层的事实结果，建立Computer已经完成构造和集成的事实。

3. OnEnable：引导系统硬件、固件、内核和应用/测试，运行应用或系统评估。

   以异步方式向QemuVirtPlatform发出Enable信号，要求硬件平台启动，Computer即完成任务并进入Online状态；后续的引导过程是QemuVirtPlatform以及后继各层的职责。

### 动作信号响应

1. OnSample：运行中响应Plant的采样信号，转化为外设中断信号，然后委托给Computer的中断控制器IntC，由IntC负责仲裁和派发。

## 正式模型

[model/systems/computer.spec](../../model/systems/computer.spec)

