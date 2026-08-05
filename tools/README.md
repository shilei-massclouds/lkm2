# LKM 模型工具

`tools/` 独立维护基于 Python 的模型工具链，包括 Python 项目配置、虚拟环境、源码、测试和命令行包装脚本。仓库其他目录不依赖这里的 Python 虚拟环境。

当前工具链处理流程为：

```text
model/main.spec → entry AST → recursive module graph → Model IR v3 → canonical JSON
entry external signals → tools/build/derive/main.sequence.json
Model IR v3 + derivation sequence → derive → canonical result JSON
```

入口只接受一条简单的 `spec IDENT;` 后接一条点分
`origin <qualified-name>;`。`spec` 与 Rust 的 `mod` 类似：根声明
`spec systems;` 装载同目录的 `systems.spec`，其中的 `spec human;`
再装载 `systems/human.spec`。只有显式声明会进入模块图，不自动发现目录，
也不使用 `<module>/main.spec`。

模块文件必须完整符合 [`module_grammar.lark`](src/modelc/module_grammar.lark)
定义的语法；未知关键字、未知声明和错误语法都会报错。Model IR v3 lowering
保留 predicate、type、object、external、state、handler、deferred 和通用表达式树。
`use` 支持 `crate`、`self`、连续
`super` 和 crate-root 裸路径；它只导入名字，不触发文件装载。当前会验证
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
make run    # 以 model/main.spec 为默认模型执行 tools/bin/derive
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
输入失败退出 1，参数错误退出 2。路径推导结果（包括失败结果）写 stdout，输入和
编译诊断写 stderr。

## Model IR、推导和缓存

`make build` 原子更新 `tools/build/modelc/model.ir.json` 和 `manifest.json`，并从
entry external 的信号按声明顺序生成
`tools/build/derive/main.sequence.json`。sequence 是构建产物，不写入 `model/`
源码目录。
manifest 记录入口和全部已声明源文件的 SHA-256、grammar/modelc/model_ir 指纹及
IR schema 版本；完全命中并能严格加载缓存 IR 时不会改写文件。`make clean` 删除
该缓存。

推导序列 schema v1 的每个事件显式指定 `source`、`target`、`signal` 和 `mode`。
derive v1 只执行 transition 与 drives/emits；attrs、reference、invariant、action、
depends_on、may_change、ensures 和 deferred 会得到 `unsupported_feature`，不会被
静默忽略。当前 Computer 没有 handler，因此仓库序列在事件 0 稳定返回
`unhandled_signal`，`make run` 的预期退出码为 1。

公共库接口为：

- `modelc.compile_spec()`
- `model_ir.load_model_ir()`
- `model_ir.dump_model_ir()`
- `derive.derive()`
- `derive.load_derivation_sequence()`
- `derive.dump_derivation_result()`

AST 保留一基、末端排他的源码范围；Model IR 不保存路径和源码位置。schema v3
loader 严格拒绝旧版本、未知字段、重复声明、无效状态引用、重复 handler 和未知
信号目标，并规范排序模块和声明。
