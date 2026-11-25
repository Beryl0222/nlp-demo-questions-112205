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

## 主持工作台

工作台只用 Python 标准库。可直接运行：

```sh
# 主持全量报告：时间账、实际回录、问句事实点、代问范围、排期、会后板、合用理由
python3 workbench.py report

# 台上提词（不显示仅主持人原话和受保护名称）
python3 workbench.py preview --role stage

# 会后承办人（只见“许可转交”和公开回录；不见“仅主持人”内容）
python3 workbench.py preview --role coordinator

# 机器可读输出
python3 workbench.py report --json
python3 workbench.py plan --json
python3 workbench.py merge Q2 Q8 --json
python3 workbench.py cluster Q1 Q2 Q8 --json

# 可选：本机网页工作台（默认只绑定 127.0.0.1:8765）
python3 workbench.py serve --port 8765
```

临时问句、短稿和新回录会写入当前目录的 `.hostdesk-state.json`，也可用
`--state /path/to/state.json` 指定：

```sh
python3 workbench.py ask "室外柜门零下五度能开吗？" --project P1 --sharing 匿名现场 --category 实际效果
python3 workbench.py draft "棚外柜门五度能用吗？" --project P1 --seconds 8
python3 workbench.py record "医院还没安排进场。" --project P2 --role 团队 --seconds 7
python3 workbench.py answer-check "没有非试用付费客户；试用在商铺且没有续订。" Q1 Q2 Q8
```

每次 `record` 都会从全部回录重新计算已用时间和剩余类别额度；随后运行
`plan`/`report` 得到的是重排结果，而不是沿用旧草稿状态。

也可作为 Python API 使用：

```python
from hostdesk import Workbench, build_plan, report_data

wb = Workbench.load("data/records.json", ".hostdesk-state.json")
print(build_plan(wb)["registered_plan"]["selected"])
print(wb.pair_merge("Q2", "Q8"))
```

## 判定口径

- 每个问句拆成若干可核实“事实点 + 限定”，并把“据说”“有续订的……”
  “医院试过以后”等列为转述或预设，不把预设当成已发生事实。
- 两个问句只有在全部事实点都能被同一个公开回答以同等限定解除时，才记为
  完整合用；部分重合只共用对应事实点，其余条件继续保留。
- `draft` 只是拟稿参考。是否已经问出，以 `readback.text` 的实际语义为准；
  例如 R1 没有说出 D1 的“支付/排除试用/续订同一家”等限定，不能把 Q1
  记为已按 D1 完整发问。
- “没说付费”解析为付款状态 `unknown/unstated`，不会误判成“未付费”。
- 时间账扣除每一段实际主持和团队发言，并按发言内容推断其公布类别。
- `现场`、`匿名现场`、`许可转交`、`仅主持人`分别控制上台、匿名代问和
  会后转交。原话有更严格限制（如“不能说是某机构”“只在会后联系”）时
  从严处理。未授权机构名出现在回录中会产生隐私警报。
- 建议短稿中的秒数只是顾问值；只有用 `draft ... --seconds N` 登记后，
  才会进入“现在可执行”的预算排期。

对当前资料，工作台给出的基线判断是：剩余 61 秒，客户核实/实际效果/量产
分别剩余 17/29/15 秒；已登记草稿优先顺序为 D4（补 P3 首次答问机会）、
D1、D3。D2 因 R2 已回答一般试用场景、D5 因 R3 已回答医院是否进场而
不再优先。Q3 走许可转交，Q6 保留为主持人可见且需补联系/转交许可。
