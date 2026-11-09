# 🎯 大转盘 (Wheel)

抽奖转盘 Web 应用。FastAPI 后端 + 静态前端,支持多奖项配置与概率设置。

## 技术栈
- Python 3 + FastAPI + uvicorn
- 原生 HTML/CSS/JS 前端(零依赖零构建)

## 运行
```bash
python3 app.py    # 默认监听 0.0.0.0:3200
```

## 目录
| 路径 | 说明 |
|---|---|
| `app.py` | FastAPI 服务与 API 路由 |
| `static/index.html` | 转盘前端页面 |
| `data/configs.json` | 奖项与概率配置 |

## API
| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/` | 页面 |
| GET | `/api/config` | 读取奖项配置 |
| POST | `/api/spin` | 执行抽奖 |

## 部署
生产环境由 systemd 托管,端口 3200;经 Caddy 反代暴露在 `/wheel/`。
