# 拾穗 Gleaners

一个基于 FastAPI + SQLite 的手机优先网页 AI Agent 演示项目，覆盖 identities、商品发布、智能匹配、订单、支付与佣金结算等流程。默认使用**零密钥仿真支付模式**，无需外部账号即可本地运行与演示。

## 快速开始

```bash
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env             # 按需修改配置
python -m uvicorn app.main:app --port 8000
```

浏览器打开 <http://127.0.0.1:8000>，健康检查结果应为 `env=dev, pay_mode=simulate`。

## 测试

```bash
python -m pytest -q
python scripts/smoke_test.py
```

## 配置说明

复制 `.env.example` 为 `.env` 后按需修改。常用项：

| 变量 | 说明 |
| --- | --- |
| `ENV` | 运行环境：`dev` / `test` / `prod` |
| `DB_URL` | 数据库连接串，默认本地 SQLite |
| `PAY_MODE` | `simulate`（默认）/ `sandbox` / `agent_pay` |
| `SECRET_KEY` / `SIM_SECRET` | 服务端密钥，生产环境务必修改 |
| `LLM_ENABLED` | 默认关闭，使用确定性兜底解析器 |
| `SEED_ON_START` | 启动时若数据库为空则自动写入种子数据 |

`.env`、本地数据库文件及缓存目录均已加入 `.gitignore`，不会提交到仓库。

## 项目结构

```text
app/
├── core/       领域基础能力（时钟、幂等、状态机、加解密等）
├── models/     数据模型
├── modules/    业务模块（身份、发布、匹配、订单、支付、佣金、调度等）
├── static/     前端页面与脚本
└── main.py     FastAPI 应用入口
tests/          自动化测试
scripts/        冒烟测试脚本
```

更多实现细节见 `.trae/documents/gleaners-backend-mvp-implementation.md`，演示流程见 `演示讲稿.md`。
