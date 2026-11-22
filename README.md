# 路演提问

桥湾创客社区将同一场路演拆成几个短场。稿件、听众及企业均为虚构。

## 资料格式

数据位于 `data/records.json`，以 UTF-8 保存。各表的记录以本表内的 `id` 或 `seq` 识别。原话保留提交时的文字。

公开性有现场、匿名现场、许可转交、仅主持人四种。仅主持人意味着本人未允转交。发言预算以秒计算，简短改写的读长由编辑在实际问题确认时登记。每个项目第一次获得可公开答问的机会优先，之后先处理日程列明的议题类别，按各类别剩余时限和可实际覆盖的问题比较。回录 text 是实际发言，draft 仅是原拟稿参考。

- `projects` 保存项目名称和投影稿。 字段为 `id`、`name`、`pitch`。
- `windows` 保存台上答问资源和优先类别。 字段为 `id`、`projects`、`total_seconds`、`categories`。
- `questions` 保存进入主持台的原始问句。 字段为 `id`、`project`、`received`、`category`、`sharing`、`text`。
- `drafts` 保存主持人存下的备选问法及读长。 字段为 `id`、`project`、`seconds`、`text`。
- `readback` 保存实际回录按实际发生顺序。 字段为 `id`、`at`、`project`、`role`、`draft`、`seconds`、`text`。

## 查看资料与自检

使用 Python 3.11.2 自带的标准库读取资料并验证字段和引用。

```sh
python3 -m json.tool data/records.json
python3 -m unittest discover -s tests -v
```
