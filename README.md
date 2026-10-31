# sukesong2026

2026 苏客松作品：**PdM Agent · 设备告警处理工作台**（AI+智能制造）。把重复告警整理成设备事件并排序，agent 调查事件、给出每条结论都有出处的处置建议，工程师审批后生成工单。

> 赛前准备的组件和比赛期间新增的内容分开记录：git tag `pre-hackathon` 之前的提交是赛前沿用组件，之后的是比赛期间完成的；`git diff pre-hackathon..HEAD` 可以看到全部现场改动。第三方代码和数据见 `THIRD_PARTY.md`。

## 运行

需要 Python 3.12+。

当前 Web 前端已迁移到 `frontend/`（Vue 3 + TypeScript + Vite + Element Plus + Pinia + Vue Router + Axios）。
前端开发服务器：

```bash
cd frontend
npm install
npm run dev
```

访问 `http://localhost:5173/`。当前前端使用演示数据，Python 检测 / Agent API 接入仍在下一阶段；原 Streamlit 页面仍可按下方命令运行。

```bash
python -m venv .venv
source .venv/bin/activate          # Windows：.venv\Scripts\activate
pip install -r requirements.txt
```

先按下面“数据”一节放好官方素材包。

事件表和检测评测（不需要模型账号）：

```bash
python -m pdm.detection          # 告警 → 事件，打印事件表和默认调查时点
python eval/detect_check.py      # 16 次故障有几次被提前预警、提前多少小时；尖峰的替代标签（附口径）
```

网页工作台（不需要模型账号，默认用离线脚本模型）：

```bash
streamlit run app/main.py        # 浏览器打开 http://localhost:8501
```

侧边栏的“当前时刻”是回放时钟：总览、队列、曲线和 agent 都只能看到这一刻之前的数据。流程：设备总览 → 事件队列 → 查看详情 → 调查 → 审批（通过 / 驳回 / 改措施）→ 工单与审批记录。审批记录存在 `data/runtime/`（不进 git）。

命令行调查一个事件，同样先用离线脚本模型：

```bash
python investigate.py EV-IM011-0920T23 --mock               # 建议处置，通过契约
python investigate.py EV-AGV031-0921T03 --mock              # 疑似传感器尖峰 → 疑似误报
python investigate.py EV-IM011-0920T23 --mock tool_error    # 数据源出错 → 调查未完成，不出工单
python investigate.py EV-IM011-0920T23 --mock fake_id       # 引用不存在的出处 → 契约两次拒绝 → 调查未完成
python investigate.py EV-IM011-0920T23 --mock future_as_of  # 模型自己填未来时间 → 被丢弃，看不到之后的故障单
```

接真实模型：复制 `.env.example` 为 `.env`，填 `LLM_BASE_URL` / `LLM_API_KEY` / `LLM_MODEL`，然后先跑冒烟测试，确认这个模型能调用工具：

```bash
python scripts/smoke_llm.py
python investigate.py EV-IM011-0920T23
python investigate.py EV-IM011-0920T23 --as-of "2026-09-21 05:00"   # 换一个调查时点
```

赛前的品质客诉骨架仍可运行（`python run.py C001 --mock`），作品不使用。

## 数据

主数据是赛事官方的仿真素材包（路演注明为脱敏仿真数据）；缺的维保、备件、知识文档为合成数据，见 `data/README.md`。

**官方素材包不随仓库提交**（能否公开待主办方确认）。复现时把主办方发放的“智能制造赛道素材包”文件夹的内容原样放进 `data/official/`，目录应为：

```text
data/official/
├── data/                    01_设备主数据.xlsx、02_设备传感器时序.xlsx、03_设备故障维修记录.csv、05_生产工单.csv … 16_EHS安全行为识别.csv（15 个文件）
├── 文档资料/                四份制度 PDF（人力、供应链、品质、物流）
└── 智能制造示例数据集.xlsx   上面 16 张表的汇总版
```

本作品读的是 `data/official/data/` 下的 01、02、03、05 四个文件。

`data/pdm/` 是赛前下载的 Azure PdM 公开数据，当前版本不使用。原生成方式：

```bash
python scripts/prepare_pdm_data.py
```

## 合成补充数据（同事提供）

维保记录、备件、设备重要性放在 `data/synthetic/`，知识文档和案例放在 `data/kb/docs/`、`data/kb/cases/`，格式见 `docs/数据格式_给同事.md`。文件放进来就生效，不用重启；没放之前，相关工具返回“还没接入”，agent 把它写进待补证据。

## 测试和评测

```bash
python -m unittest discover -s tests     # 单元测试
python eval/make_golden_template.py      # 生成题集标注模板 eval/pdm_golden.csv（已存在不覆盖）
python eval/run_pdm_eval.py --mock       # PdM 题集评测流程自检（离线）
python eval/run_pdm_eval.py --trials 3   # 真实模型跑已标注的题，看 pass^3
python eval/run_eval.py --mock           # 赛前客诉题集评测（作品不使用）
python eval/run_eval.py                  # 用真实模型跑题集
```
