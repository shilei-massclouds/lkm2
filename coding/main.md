# 生成代码约束

`coding/` 将模型约束收敛为实现规则，并与 `model/`、`impl/` 保持稀疏的目录对应关系。
当前实现入口链为：

```text
impl/main.rs -> impl/phases.rs -> impl/phases/arch_head.rs
```

实现必须直接使用固定 Rust 工具链及其 sysroot crate。禁止引入 Cargo，禁止从 registry、
Git 或 vendor 目录取得外部 crate；允许的代码来源只有本仓库源码和固定工具链自带的
sysroot crate。

仓库采用固定 sibling 布局：`<parent>/lkm2` 与 `<parent>/linux-6.12`。后者是只读的
Linux 机制参考和未来差分基线，不是当前阶段的构建依赖，构建脚本也不得强制检查其
存在。

当前架构范围仅限 RISC-V 64 位。该限制同时适用于 LKM2 实现和
`../linux-6.12` sibling 参考/差分基线；当前不支持其他架构。
后续实现内容的机制、顺序和差分语义应参考该 sibling 中的 RISC-V 64 位实现。
