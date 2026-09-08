# 新 main 前后端运行检查记录

检查日期：2026-09-04

## 范围

本次按用户要求回到拉取新 `main` 后的代码状态，撤销了本轮旧版前端 HCI 修正及其工作记录；未保留旧版审查任务的实现。用户原有的 `frontend/package-lock.json` 修改未触碰。

## 本地运行配置

- 已在被 `.gitignore` 忽略的 `backend/.env` 中配置本机 MySQL 连接。
- 凭据未写入 Git、此文档或命令输出。
- MySQL80 服务运行中，3306 端口可达。

## 验证结果

- 前端：`frontend/npm.cmd run build` 通过，TypeScript 与 Vite 构建成功。
- 后端：使用仓库 `.venv` 的 Python 3.13 环境，FastAPI、Uvicorn、SQLAlchemy、Pydantic Settings、HTTPX 均可导入。
- 后端服务：`http://127.0.0.1:8000/` 返回 `200`；OpenAPI 返回 `200`，包含前端所需路由。
- 数据库接口：`/api/v1/metadata/filter-options` 返回 `200`，真实筛选项可读。
- 图谱接口：`/api/v1/graph?depth=1&limit_nodes=12&selection_mode=global` 返回 `200`，真实节点、边和边组可读。
- 搜索接口：以 `squalene` 查询返回 `200` 和分页结果。
- 详情接口：以 `ENZ000049` 查询返回 `200`，包含序列、基因、反应、证据等详情。
- 结构检索：以已存在 InChIKey 查询返回 `200`，包含化合物和反应结果。
- 下载预览：返回 `200`，列名、行数和文件名正确。
- 下载生成及文件服务：返回 `200`，生成的 CSV 可通过 `/api/v1/downloads/...` 读取；测试文件已清理。
- Vite 代理：通过 `http://127.0.0.1:5173/api/v1/...` 访问元数据和图谱均返回 `200`。
- Ketcher 静态资源：编辑器入口和主脚本均返回 `200`。
- 后端 Python `compileall` 通过；由此产生的已跟踪缓存改动已恢复。

## 当前状态

前后端已正常运行并完成真实数据库联通。当前工作区只剩用户原有的 `frontend/package-lock.json` 未提交修改；本地 `backend/.env` 被忽略，不会进入版本控制。

## 运行地址

- 前端：<http://127.0.0.1:5173/>
- 后端：<http://127.0.0.1:8000/>
- API 文档：<http://127.0.0.1:8000/docs>
