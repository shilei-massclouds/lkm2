# LKM 模型工具

`tools/` 独立维护基于 Python 的模型工具链，包括 Python 项目配置、虚拟环境、源码、测试和命令行包装脚本。仓库其他目录不依赖这里的 Python 虚拟环境。

当前工具链处理流程为：

```text
model/main.spec → entry AST → recursive module graph → Model IR v4 → canonical JSON
entry external signals → tools/build/derive/main.sequence.json
Model IR v4 + external-root sequence v2 → derive → result JSON v2 / human stdout
```

有状态对象可以省略 `initial_state`；modelc 会将其规范化为
`State::Base`。因此这类对象必须声明 `state State::Base`。显式指定的初始状态保持
不变，无状态对象仍没有初始状态。Model IR v4 的严格 JSON schema 没有变化：
`initial_state` 字段始终必需，有状态对象的默认值会明确写成
`["State", "Base"]`，无状态对象写成 `null`。

入口接受一条或多条简单的 `spec IDENT;`，然后是一条点分
`origin <qualified-name>;`。每条入口 `spec` 都声明一个并列根模块；第一条是
Model IR v4 `entry.spec` 中的主根。当前模型由 `systems`、`objects`、
`flows` 三个并列根模块组成。`spec` 与 Rust 的 `mod` 类似：入口根
声明 `spec systems;` 装载同目录的 `systems.spec`，其中的
`spec human;` 再装载 `systems/human.spec`。只有显式声明会进入模块图，
不自动发现目录，也不使用 `<module>/main.spec`。

模块文件必须完整符合 [`module_grammar.lark`](src/modelc/module_grammar.lark)
定义的语法；未知关键字、未知声明和错误语法都会报错。Model IR v4 lowering
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
该输出是精简因果视图，只展示信号的 drives/emits 关系、单元进入状态和提交结果。
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

推导序列 schema v2 的每个事件显式指定 `source`、`target`、`signal` 和 `mode`，
但 sequence 只选择外部根信号；drives 与 emits 由引擎按因果关系自动调度。
结果 JSON schema v2 记录嵌套推导单元、逐项条件、最终状态和 predicate facts；
`dump_derivation_result()` 提供包含完整检查细节的规范 JSON，CLI 默认使用精简的
人类因果 renderer。当前支持
Transition 与 Action 信号、对象状态比较、布尔组合、depends_on、drives、ensures、
establishes、状态 invariant 和深度优先 emits；Action 提交事实但不改变状态。
信号名使用完整的 `Transition::<Name>` 或 `Action::<Name>` 精确匹配。
`Transition::Preset` 是正式名称；`Transition::Startup` 只在兼容输入边界可用，
并会在进入核心前立即规范化为 `Transition::Preset`。兼容边界包括 `.spec` 中
`drives`、`emits`、`external` 的 signal 调用，以及内存或 sequence JSON 的
derive 请求。Transition handler 必须声明 `Transition::Preset`；Model IR JSON
和 result JSON 只接受、保存并输出正式名称。`Action::Startup` 不属于该别名。
attrs、reference、may_change、deferred 及其他表达式会明确返回
`unsupported_feature`。当前 Computer 按顺序处理 `Preset`、`Setup` 和 `Enable`，
依次提交到 `State::Prepared`、`State::Ready` 和 `State::Online`；Kernel 的
Startup 兼容调用会以正式的 Preset 信号启动 BootInitFlow，再完成 Setup 和 Enable。
默认 `make run` 输出完整推导，与结论空开一行，并以 `Derivation passed!` 和退出码 0 结束。

公共库接口为：

- `modelc.compile_spec()`
- `model_ir.load_model_ir()`
- `model_ir.dump_model_ir()`
- `derive.derive()`
- `derive.load_derivation_sequence()`
- `derive.dump_derivation_result()`
- `derive.load_derivation_result()`
- `derive.render_derivation_result()`

AST 保留一基、末端排他的源码范围；Model IR 不保存路径和源码位置。schema v4
loader 严格拒绝旧版本、未知字段、重复声明、无效状态引用、重复 handler 和未知
信号目标，并规范排序模块和声明。
