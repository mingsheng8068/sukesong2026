# 第三方组件与数据来源

按《授权及承诺书》第九条和选手手册要求列出：作品用到的所有外部代码、数据集和模型，以及各自的许可证。本项目没有使用任何公司（参赛者雇主及其客户）的数据、代码或算法。

## 数据集

| 名称 | 来源 | 许可证 | 用法 |
|---|---|---|---|
| 智能制造赛道素材包（16 张仿真业务表 + 4 份制度文档） | 2026 苏客松主办方，10-30 在 AI+智能制造赛道答疑群发布 | 素材包说明页：“仿真业务数据，供黑客松原型开发使用”；路演须注明“数据为脱敏仿真数据，方案可迁移至真实 MES/SCADA/QMS 数据” | 主数据。读取设备主数据、传感器时序、故障维修记录、生产工单；原样放在 `data/official/`，未修改；能否公开待确认，暂不随仓库提交（见 README“数据”） |
| Azure Predictive Maintenance（machines / errors / failures / maint / telemetry） | [Microsoft/SQL-Server-R-Services-Samples · PredictiveMaintenanceModelingGuide/Data](https://github.com/Microsoft/SQL-Server-R-Services-Samples/tree/master/PredictiveMaintenanceModelingGuide/Data) | CC BY 4.0（仓库 LICENSE；代码部分为 MIT，见 LICENSE-CODE） | 只取 1–100 号机器，由 `scripts/prepare_pdm_data.py` 生成 `data/pdm/*.csv.gz`；原始数据为微软模拟生成，不是真实设备 |
| 客诉 / 批次 / 检验 / 案例（`data/*.json`） | 本项目自行编写 | 本项目 | 全部为虚构的合成数据，见 `data/README.md` |

Azure PdM 数据署名：Microsoft Corporation，Predictive Maintenance Modeling Guide，CC BY 4.0。本项目对其做了子集抽取（machineID ≤ 100）和 gzip 压缩，未修改数值。

## Python 依赖

| 包 | 版本 | 许可证 |
|---|---|---|
| openai | 3.35.1 | Apache-2.0 |
| streamlit | 1.66.1 | Apache-2.0 |
| pandas | 3.1.0 | BSD-3-Clause |
| rank-bm25 | 0.2.2 | Apache-2.0 |
| jieba | 0.42.1 | MIT |
| openpyxl | 3.1.5 | MIT |

## 模型

通过 OpenAI 兼容接口调用，地址和模型名在 `.env` 里配置，代码不绑定某一家。比赛现场实际使用的模型在提交时补记于此：

- ［现场填写］服务商 / 模型名 / 使用条款链接
