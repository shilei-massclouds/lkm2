# LKM 模型工具

`tools/` 独立维护基于 Python 的模型工具链，包括 Python 项目配置、虚拟环境、源码、测试和命令行包装脚本。仓库其他目录不依赖这里的 Python 虚拟环境。

当前 `modelc` 处理流程为：

```text
model/main.spec → entry AST → recursive module graph → Model IR v2 → canonical JSON
```

入口只接受一条简单的 `spec IDENT;` 后接一条点分
`origin <qualified-name>;`。`spec` 与 Rust 的 `mod` 类似：根声明
`spec systems;` 装载同目录的 `systems.spec`，其中的 `spec human;`
再装载 `systems/human.spec`。只有显式声明会进入模块图，不自动发现目录，
也不使用 `<module>/main.spec`。

模块文件必须完整符合 [`module_grammar.lark`](src/modelc/module_grammar.lark)
定义的语法；未知关键字、未知声明和错误语法都会报错。当前 IR lowering 只保留
模块图，其他已解析 DSL 声明尚不进入 IR。`use` 支持 `crate`、`self`、连续
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
make build  # Python 源码编译检查
make test   # 运行 unittest 测试
make run    # 编译 model/main.spec 并输出 JSON
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

## modelc 命令行

仓库包装脚本会优先使用 `tools/.venv`，并且可以从任意工作目录调用：

```sh
tools/bin/modelc model/main.spec
tools/bin/modelc -o model.json model/main.spec
```

激活虚拟环境后，也可以直接使用安装生成的 `modelc` 命令。未指定 `-o` 时 JSON 写入标准输出；诊断只写入标准错误。成功、编译或 I/O 失败、命令行参数错误的退出码分别为 0、1、2。

## 当前 Model IR

`model/main.spec` 会生成：

```json
{
  "entry": {
    "origin": [
      "systems",
      "human",
      "Human"
    ],
    "spec": [
      "systems"
    ]
  },
  "modules": [
    {"name": ["systems"]},
    {"name": ["systems", "computer"]},
    {"name": ["systems", "human"]}
  ],
  "schema_version": 2
}
```

公共库接口为：

- `modelc.compile_spec()`
- `model_ir.load_model_ir()`
- `model_ir.dump_model_ir()`

AST 保留一基、末端排他的源码范围；Model IR 不保存路径和源码位置。
schema v2 的 loader 严格拒绝 v1、未知字段和无效模块图，并把模块按绝对
限定名规范排序。
