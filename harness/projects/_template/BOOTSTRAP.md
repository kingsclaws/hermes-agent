# BOOTSTRAP.md — 法律项目初始化参考

> 本文件是项目初始化的参考模板。实际项目创建通过 `hp init` 完成——
> 运行 `setup.sh new-project <path>` 会自动调用 `hp init` 并注入法律上下文。
> 初始化完成后，项目状态由 `.hermes-project/project-context.md` 维护。

## 项目基本信息
- **项目名称**：（待填写 — hp init --name）
- **项目简称**：（用于文件命名，如 SJ、FOSUN）
- **客户名称**：（待填写 — hp init --client）
- **项目类型**：尽调报告 / 合同审阅 / 法律意见书 / 备忘录 / 诉讼文件 / 其他
- **工作目录**：（hp init --cwd）
- **创建日期**：（自动填写）

## 项目目标
（hp init --goal）

## 关键约束
- 交付截止日期：（待填写）
- 格式标准：项目根目录 STANDARDS.md
- 文件命名：`<项目简称>_<文档类型>_<版本号>_<日期>.docx`

## 参考文件
（列出所有输入文件、模板、参考文档的路径）

## 创建命令示例

```bash
# 通过 setup.sh 创建（自动配置 legal 默认值）
./setup.sh new-project /path/to/project \
    --name "弘郡二期" \
    --client "中信金资" \
    --goal "起草担保合同纠纷诉状"

# 或直接用 hp init
hp init \
    --cwd /path/to/project \
    --name "弘郡二期" \
    --client "中信金资" \
    --goal "起草担保合同纠纷诉状" \
    --profile-name hpswarm-coordinator \
    --toolsets lex-docx

# 创建后启动
cd /path/to/project && hermes chat -p hpswarm-coordinator
```
