# API 与 MCP

控制台 API 根地址为 `http://127.0.0.1:8769`。所有 `/api/` 请求需 `Authorization: Bearer <本机令牌>`；令牌存储于 `private/console/access-token.txt`。不对公网开放，无 CORS。`GET /health` 只返回服务标识。

性能观测：任务增加 started_at、queue_seconds、ready_at。播放器报告 playing 后记录 first_played_at、delivery_seconds，后者是音频完成到播放器上报的时间，不含 ChatGPT 推理或进入本机前的隧道时间。合成结果的 acceleration 显示实际启用的后端，未启用则为 ordinary。冷启动时 generation_seconds 不含首次 Python 模块导入；应使用 ready_at-created 观察本机请求完整等待。

| 方法 | 路径 | 用途 |
|---|---|---|
| GET | /api/state | 公开音色元数据、设置、最近任务、引擎和播放器状态 |
| POST | /api/settings | speed、autoplay、speech_enabled、default_voice、role_bindings、desktop_playback |
| POST | /api/speak | text、voice_id/role、language、speed、hide_text；返回排队任务 |
| GET | /api/jobs/{id} | 状态 queued/generating/ready/error/cancelled，完成后含 audio_path |
| GET | /api/audio/{id}.wav | 真实 PCM WAV，须任务完成且有令牌 |
| POST | /api/control | action: pause/resume/stop；发送命令，不假称播放完成 |
| POST | /api/playback | 本机播放器 heartbeat：state、job_id |
| POST | /api/reveal | job_id；用户主动揭示原文 |
| GET | /api/references/{voice_id} | 可用参考录音元数据及 locked 状态 |
| POST | /api/reference | voice_id、index；J 禁止覆盖参考 |
| POST | /api/import-voice?name=... | ZIP 二进制流；须 X-Model-Trust: yes |
| GET | /api/export | 无模型、音频、令牌和路径的设置备份 |
| POST | /api/import-settings | 导入设置备份；未知音色 ID 拒绝 |

朗读参数：text 1～400 字；language 可为 zh/en/ja/ko/yue（语言质量分别验收）；speed 0.6～1.5，省略使用已保存默认值。role 未绑定、voice_id 不存在、二者冲突均报错。工具不把错误角色回退到默认音色。

MCP 2.3.0，协议 stdout，日志 stderr：

| 工具 | 行为 |
|---|---|
| linguavoice_list_voices | 可用音色、设置、角色绑定和播放状态 |
| linguavoice_speak | AI 提供选中的文字，真实合成，返回 AudioContent + 元数据 |
| linguavoice_get_audio | 按任务 ID 获取已有结果，不重新合成 |
| linguavoice_playback | 请求本机播放器 pause/resume/stop |
| linguavoice_open_console | 在后端电脑上打开设置控制台，不在手机开本机页面 |

一次 speak 最多等待 85 秒；超时返回任务 ID，继续用 get_audio 获取，不能重复提交相同任务。单段 WAV 大于 6MiB 时保留本机播放，不嵌入工具结果，以给隧道 10MiB 序列化限制留余量。听力原文不在返回正文暴露，但调用参数本身仍可能被客户端显示。

取消不强制打断正在运行的 CUDA 内核：完成当前计算后弃用结果。后台模式由电脑输出线程执行暂停、继续、停止；网页模式由浏览器轮询执行。状态区分已请求、正在播放、播放完成和输出失败。

扩展 TTS：实现 `SpeechBackend.synthesize(voice,text,language,speed,destination)` 和 `unload()`，在服务层注入适配器。当前 GPTSoVITSBackend 与 QwenBackend 已真实合成验证；云端后端需独立认证、费用授权和测试，不能只改 provider 字符串宣称支持。


### 异步安装

`POST /api/install` 接收 ZIP 二进制流，保留 Bearer 与 `X-Model-Trust: yes` 检查，返回 202 和安装任务 id。`GET /api/installs/{id}` 返回 progress、message、status；状态为 installing / ready / needs_setup / error。ready 表示文件安装及必要组件检查完成，不等于音质已经验收。needs_setup 表示角色已保存但基础组件缺失。任务记录在内存，重启后重新拖入包会按内容去重。临时上传文件保存在本机 private/console/uploads 并在完成或失败时清理，不写入系统临时目录。

`POST /api/rename-voice` 接收 voice_id 与 name，仅改变显示名称，不改变角色绑定、模型和参考录音。原 `/api/import-voice` 同步接口保持兼容。


### 后台播放与登录启动

- `POST /api/startup`：`{"enabled": true/false}`，设置本用户登录启动；启用时立即启动守护程序，不要求管理员。
- `POST /api/replay`：`{"job_id": "..."}`，重播已有音频，不重复合成；需开启语音和后台模式。
- `/api/state` 增加 background（服务/连接就绪情况、登录启动开关）以及 playback.target=desktop/browser。任务含 playback_target、playback_status、playback_error；输出流完成后才记为 ended。
- 角色公开元数据包含 provenance：作者、来源、许可说明、核查日期和许可状态。不把“来源已核对”当作任意再分发授权。
- `/api/catalog` 提供已知角色的作者来源元数据，不代理不明下载地址、不提供角色权重导出。

## 模型后端与参考声音包

`private/runtimes.json` 记录每个后端的 Python 与模型位置，仅供本机使用，不公开。BackendRouter 为每个后端启动独立进程，切换后端先结束旧进程释放显存。Qwen 与 GPT-SoVITS 不混用 Transformers 依赖。

Qwen 参考包的 voice.json 格式：
```json
{"format":"linguavoice-qwen-reference","schema_version":1,"name":"自定义声音","reference":{"audio":"reference.wav","text":"与录音完全对应的台词","language":"zh"}}
```
录音路径必须位于 ZIP 内，长度 3～30 秒；声音提示由本机模型生成。来源许可在导入时由用户确认。GPT-SoVITS 参考为 3～10 秒。

`GET /api/support` 返回版本、语言、导入格式、功能和当前安装的声音。真实测试标记保存在本机验证文件中；未测试的安装不会继承其他电脑的“本机已实测”。
