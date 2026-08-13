# LKM 模型工具

`tools/` 独立维护基于 Python 的模型工具链，包括 Python 项目配置、虚拟环境、源码、测试和命令行包装脚本。仓库其他目录不依赖这里的 Python 虚拟环境。

当前工具链处理流程为：

```text
model/main.spec → entry AST → recursive module graph → Model IR v10 → canonical JSON
entry external signals → tools/build/derive/main.sequence.json
Model IR v10 + external-root sequence v3 → derive → result JSON v9 / human stdout
```

有状态对象可以省略 `initial_state`；modelc 会将其规范化为
`State::Base`。因此这类对象必须声明 `state State::Base`。显式指定的初始状态保持
不变，无状态对象仍没有初始状态。Model IR v10 的严格 JSON schema 中：
`initial_state` 字段始终必需，有状态对象的默认值会明确写成
`["State", "Base"]`，无状态对象写成 `null`。

入口接受一条或多条简单的 `spec IDENT;`，然后是一条点分
`origin <qualified-name>;`。每条入口 `spec` 都声明一个并列根模块；第一条是
Model IR v10 `entry.spec` 中的主根。当前模型由 `systems`、`objects`、
`phases`、`flows` 四个并列根模块组成。`spec` 与 Rust 的 `mod` 类似：入口根
声明 `spec systems;` 装载同目录的 `systems.spec`，其中的
`spec human;` 再装载 `systems/human.spec`。只有显式声明会进入模块图，
不自动发现目录，也不使用 `<module>/main.spec`。

模块文件必须完整符合 [`module_grammar.lark`](src/modelc/module_grammar.lark)
定义的语法；未知关键字、未知声明和错误语法都会报错。Model IR v10 lowering
保留 predicate、type、object、external、state、handler、deferred 和通用表达式树。
绝对 `use` 路径使用 `model::`，相对路径使用 `self::` 或连续的
`super::`；不带根关键字的裸路径也从 model root 开始。`crate::` 不再受支持。
`use` 只导入名字，不触发文件装载。当前会验证
模块前缀和重复本地导入名，但不会验证路径末端对象是否存在。

入口语法由 [`grammar.lark`](src/modelc/grammar.lark) 定义。模块语法当前定义的
顶层声明为 `spec`、`use`、`predicate`、`type`、`object` 和 `external`；对象、
状态、handler、deferred 与表达式的合法结构同样直接写在模块 grammar 中，
不另设“禁止关键字”列表。

## 环境设置

需要 Python 3.11 或更新版本以及系统的 `venv` 支持。从仓库根目录执行：

```sh
make setup
```

也可以直接调用组件目标：

```sh
make -C tools setup
```

该目标创建 `tools/.venv`，然后根据 `tools/pyproject.toml` 以 editable 模式安装工具及 Lark 依赖。虚拟环境和 Python 构建产物都限制在 `tools/` 下。

## 构建和测试

根 Makefile 会把对应目标委托给 `tools/Makefile`：

```sh
make build  # Python 源码编译检查并更新持久化 Model IR 缓存
make test   # 运行 unittest 测试
make test-derive  # 运行 derive 单元测试和 golden 冒烟测试
make test-smoke   # 只运行 derive golden 冒烟案例
make run    # 以 model/main.spec 为默认模型执行 tools/bin/derive
make run VERBOSE=1  # 同上，并显示 Make 委托、构建步骤和 derive 命令
```

直接使用组件 Makefile 的等价命令为：

```sh
make -C tools build
make -C tools test
make -C tools run
```

这些 Makefile 目标会自行选择 `tools/.venv`，无需手动激活环境。只有在交互式使用 Python、pip 或安装生成的命令时，才需要选择性激活：

```sh
source tools/.venv/bin/activate
```

## 命令行

仓库包装脚本会优先使用 `tools/.venv`，并且可以从任意工作目录调用：

```sh
tools/bin/modelc                         # 默认 model/main.spec
tools/bin/modelc model/main.spec
tools/bin/modelc -o model.json model/main.spec
tools/bin/derive                         # 默认 --model model/main.spec
tools/bin/derive --model model/main.spec
tools/bin/derive --model tools/build/modelc/model.ir.json
tools/bin/derive --sequence tools/build/derive/main.sequence.json
make run MODEL=model/main.spec
make run SEQUENCE=tools/build/derive/main.sequence.json
```

激活虚拟环境后，也可以直接使用安装生成的 `modelc` 和 `derive` 命令。
`--model` 接受 `.spec` 或 Model IR JSON，并从 entry external 的信号生成默认序列；
`--sequence` 使用默认的 `model/main.spec`。两者互斥。成功退出 0，路径推导失败或
输入失败退出 1，参数错误退出 2。人类可读的推导过程（包括语义失败）写 stdout；
该输出是精简因果视图，展示 drives/emits/resumes 因果关系、实参、单元进入状态和提交结果。
depends_on、ensures、establishes、invariant、handler 匹配及其逐项结果保留在结果
JSON 中。输入和编译诊断写 stderr。默认 `make run` 隐藏 Make 委托、构建步骤和
derive 命令行，只保留推导输出或错误诊断；只有 `VERBOSE` 严格等于 `1` 时恢复
全部命令回显。这个静默行为只作用于 `run` 的构建前置步骤，独立的 `make build`
和 `make test` 保持原有输出。

## Model IR、推导和缓存

`make build` 原子更新 `tools/build/modelc/model.ir.json` 和 `manifest.json`，并从
entry external 的信号按声明顺序生成
`tools/build/derive/main.sequence.json`。sequence 是构建产物，不写入 `model/`
源码目录。
manifest 记录入口和全部已声明源文件的 SHA-256、grammar/modelc/model_ir 指纹及
IR schema 版本；完全命中并能严格加载缓存 IR 时不会改写文件。`make clean` 删除
该缓存。

推导序列 schema v3 的每个事件显式指定 `source`、`target`、`signal`、`mode` 和
实参，但 sequence 只选择外部根信号；drives、emits 与 resumes 由引擎按因果关系
自动调度。结果 JSON schema v9 顶层记录有序路径集合；每条路径保留嵌套推导单元、
逐项条件、最终状态、运行时字段/Collection 值、predicate facts、continuation
frame、参数绑定快照、线路 `current_task_ref`，以及 Scheduler 的 idle/runq 上下文；
`dump_derivation_result()` 提供包含完整检查细节的规范 JSON，CLI 默认使用精简的
人类因果 renderer。当前支持
Transition 与 Action 信号、对象状态比较、布尔组合、depends_on、drives、ensures、
establishes、状态 invariant、深度优先 emits、提交后的 resumes，以及
continuation Action 的同步 drives、yields 和断点恢复。Action/Transition 可以声明
对象类型参数并向条件、facts、updates 和下游 signal 转发。`updates` 与状态、facts
原子提交；mutable 对象引用字段和内建唯一 FIFO `Collection<T>.Action::Enqueue(T)`
可直接执行。
信号名使用完整的 `Transition::<Name>` 或 `Action::<Name>` 精确匹配。
`Transition::Preset` 是正式名称；`Transition::Startup` 只在兼容输入边界可用，
并会在进入核心前立即规范化为 `Transition::Preset`。兼容边界包括 `.spec` 中
`drives`、`emits`、`resumes`、`external` 的 signal 调用，以及内存或 sequence JSON 的
derive 请求。Transition handler 必须声明 `Transition::Preset`；Model IR JSON
和 result JSON 只接受、保存并输出正式名称。`Action::Startup` 不属于该别名。
type 支持单继承；modelc 按 base type、derived type、object 展开字段、state、
invariant 和 handler，并检查显式 `override`、抽象 handler、继承环与 continuation
的唯一 `State::Online` 生命周期。`continuation: true` 只能由 type 声明且不可取消。
continuation 的 `yields` 目标立即深度优先执行；未启动时 `resumes X.Action::Enter`
从默认入口开始，yield 后从保存断点恢复，完成后再次 resumes 返回
`no_resumable_continuation`。外部进入 continuation 必须使用 resumes，普通 emits
不得指向 continuation；同一 continuation 内的其它 Action 仍可同步 drives。
reference、may_change、deferred 及其它未实现表达式会明确返回
`unsupported_feature`。当前 Computer 按顺序处理 `Preset`、`Setup` 和 `Enable`，
依次提交到 `State::Prepared`、`State::Ready` 和 `State::Online`；Kernel 的
Enable 提交后只驱动 `BootTask.Transition::Resume`。Task Resume handler 可用
`resumes self.ResumeTargetRef.Action::Enter` 显式决定是否进入恢复点；推导器把该动态
selector 解析为唯一 parent TaskFlow 或 parked UserAppRuntime。`self.TaskFlowRef` 始终
指向初始 TaskFlow；模型不得通过具名 TaskFlow 直接进入。
`sched_core: true` 类型为实例隐式提供无参数 `Action::Enqueue` 和
`Action::Dequeue`。这两个信号只能由 Task 对象发出，分别把 source Task 加入或
移出实例私有的隐藏 runnable 集合；重复加入、不存在的删除以及尝试加入 idle Task
都会产生明确失败码。Task Suspend/Resume handler 不得调用这两个信号。
公开 `derive()` 首先构造唯一 CPU 推导线路：模型必须恰有一个 sched_core 实例，
线路从其 idle Task 预置只读 `CurrentTaskRef`。零个或多个 Scheduler 返回
`invalid_derivation_line`，此时结果的 `current_task_ref` 为 null；有效线路中它始终
是具体 Task。`switches name;` 在 Scheduler Action 中对 runq 的每个唯一成员各展开一条
候选路径；仅当 runq 为空时才绑定 idle Task。内部 list 顺序只用于稳定输出，不表达
FIFO 或任何调度策略。switch 本身不改变 membership，也不隐式执行 Suspend 或 Resume。`CurrentTaskRef`
是任意 handler 可只读使用的线路 Task selector；switches 绑定也是运行时 Task target。
Scheduler 不拥有 current。Schedule 先验证线路 current Task 为 OnCpu，再执行
Suspend、候选选择和 Resume；Resume 以及 Scheduler handler 校验全部成功后，
推导器先原子提交线路 current，再执行所选 Resume handler 的 model-declared deferred
resumes。提交前失败会丢弃这些 resumes 并保留旧 current；恢复入口自身失败不回滚切换。
结果总体状态在任一路径失败时为 `failed`，否则在存在 suspended 路径时为
`yielded`，其余为 `passed`；CLI 对多路径按稳定顺序分段输出，并在总体失败时返回 1。

默认 `make run` 输出完整推导，与结论空开一行。BootSetup 将 Scheduler 推进到
Online 并启用 `KernelInitTask`；其 Enable 生命周期驱动隐藏 runq 的 Enqueue；
Suspend 与 Resume 不改变 runnable membership。BootTask 初始为 Online 且兼作
idle Task，始终位于 runq 之外；Kernel 首次 Resume 使其进入 OnCpu。切换到
`KernelInitTask` 后，`UserRunPhase`
通过 `CurrentTaskRef.UserAppRuntimeRef` 同步完成推导器按需生成的
`KernelInitTask.UserAppRuntime` 的 Preset、Setup、Enable，再 yield 到其
`Action::Enter` 用户态黑盒入口。`user_runtime: true` 指示推导器为每个 episode 从该
入口触发一次普通 Schedule；KernelInitTask Suspend 前后都留在 runq 中，并在唯一候选
线路中再次选中；Resume 回到同一 Runtime 坐标时只确认 episode 已恢复，不再递归调度。推导保留
BootHandoff 与 UserRunPhase continuation，以 `yielded` 结束；BootIdle 及其 panic
不可达，CLI 返回 0。Model IR schema 仍为 v10。

公共库接口为：

- `modelc.compile_spec()`
- `model_ir.load_model_ir()`
- `model_ir.dump_model_ir()`
- `derive.derive()`
- `derive.load_derivation_sequence()`
- `derive.dump_derivation_result()`
- `derive.load_derivation_result()`
- `derive.render_derivation_result()`

AST 保留一基、末端排他的源码范围；Model IR 不保存路径和源码位置。schema v10
loader 严格拒绝旧版本、未知字段、重复声明、无效状态引用、重复 handler 和未知
信号目标，并规范排序模块和声明。
Derivation Result schema v9 同样严格拒绝旧 `selections`、Scheduler `current_task`
字段与旧 schema，仅输出
事务式 `switches` 记录。
