# Changelog / 更新日志

所有关于 `astrbot_plugin_chat_mode` 插件的重要改动均记录在此文件中。

---

## [1.6.0] - 2026-08-21

### 新增 (Added)
- **生图规范可视化配置**：新增 `rp_image_prompt_spec` 配置项，允许用户在后台 WebUI 设置面板中直接查看、自由编辑和自定义更新提炼生图 Tag 时注入给 LLM 的 System Prompt。
- **动态回退策略**：用户自定义生图规范留空时自动回退为内置最新默认生图规范。

### 变更 (Changed)
- **更新默认生图规范**：优化内置与外部共享的 NAI Tag 提炼规范，提升画面角色一致性与第一人称/POV 视角遵循能力。

---

## [1.5.0] - 2026-08-21

### 优化 (Changed)
- **插件市场规范合规**：根据《AstrBot 插件市场规范（2026-06-27）》规范化 `metadata.yaml` 字段（统一为 `desc`，补全 `category`、`tags`、`logo`）。
- **国际化多语言支持**：新增 `.astrbot-plugin/i18n/` 标准目录，提供 `zh-CN.json` 与 `en-US.json` 中英双语插件元数据。

---

## [1.4.0] - 2026-08-21

### 新增 (Added)
- **生图超时时间配置**：
  - `rp_image_prompt_timeout`：配置调用 LLM 提炼生图 Tag 的最大超时时间（默认 60s）。
  - `rp_image_gen_timeout`：配置调用 NAI 生图插件生成图片的最大超时时间（默认 240s）。

### 修复 (Fixed)
- **超时异常平滑处理**：捕获 `TimeoutError` 并输出友好的警告日志，避免底层网络卡顿导致任务抛出截断异常。

---

## [1.3.0] - 2026-08-20

### 新增 (Added)
- **独立 Tag 提取模型**：新增 `rp_image_prompt_provider_id` 配置项，支持单独指定用于提炼生图 Tag 的 LLM Provider（支持在 WebUI 中下拉选择），未配置时自动回退至当前会话模型。

---

## [1.2.0] - 2026-08-20

### 修复 (Fixed)
- **System Prompt 空安全**：修复 `req.system_prompt` 为 `None` 时调用 `.strip()` 抛出 `AttributeError` 导致整段 RP 规则块被静默吞掉的 Bug。
- **后台生图任务 GC 防丢**：引入 `_bg_tasks` 集合持有强引用，防止生图等待期间协程 Task 被 Python 垃圾回收销毁。
- **Prompt 措辞统一**：去除相互矛盾的"自然语言"指令，全面切换为 NAI 逗号分隔的纯 Tag 格式。

---

## [1.1.0] - 2026-08-17

### 新增 (Added)
- **RP 场景插图联动**：RP 模式下支持按概率联动 `astrbot_plugin_nai_image` 生成当前剧情场景插画。
- **防刷图冷却机制**：支持配置触发概率 `rp_image_probability` 与冷却时间 `rp_image_cooldown`。
- **插件图标与展示**：添加插件 Logo 与更丰富的展示信息。

---

## [1.0.0] - 2026-08-16

### 初始发布 (Initial Release)
- **日常 / RP 双模式切换**：支持 `/rp on [场景]`、`/rp off`、`/rp status`。
- **三层深度隔离**：
  - 系统提示词优先级覆写与日常表达提醒注入；
  - 会话级别独立的对话历史隔离；
  - 长期记忆（LivingMemory / MemoryCompanion）召回风格清洗。
