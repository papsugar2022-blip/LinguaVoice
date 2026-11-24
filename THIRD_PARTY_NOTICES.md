# 第三方组件与资源

LinguaVoice 自有代码采用 MIT。以下组件和资源不因本项目开源而改用 MIT：

| 组件 | 此机版本/来源 | 许可/说明 |
|---|---|---|
| GPT-SoVITS | bilibili/GPT-SoVITS，6d51ffce… | 仓库 LICENSE 为 MIT；保留上游版权，模型另查 |
| MCP Python SDK | 2.3.0 | 包元数据 MIT |
| Rich | 15.0.0 | 包元数据 MIT；上游加速模块的运行依赖，不重分发发行包 |
| Pygments | 2.21.0 | 包元数据 BSD-2-Clause；Rich 传递依赖 |
| PyTorch | 2.9.1+cu128 | 包元数据 BSD-3-Clause；二进制依赖另含声明 |
| torchaudio | 2.9.1 | 需保留发行包 LICENSE；本包不重分发依赖 |
| transformers | 4.57.6 | 包元数据 Apache 2.0 |
| NumPy | 1.26.4 | BSD 许可及发行包内第三方声明 |
| soundfile | 0.14.0 | BSD-3-Clause；libsndfile 单独许可 |
| librosa | 0.10.2 | ISC |
| OpenAI tunnel-client | 官方 release | 独立下载；遵守该发行包附带 LICENSE/第三方声明，不纳入源码包 |
完整 Python 版本快照见 requirements-tested.txt；上述是当前直接相关组件核对，不宣称完成所有传递依赖和预训练底模的公开再分发审计。未来公开完整安装包前需逐项复查对应版本、捆绑库与资源条款。


## 0.2 安装入口与声音来源

- Windows 安装器只捆绑 LinguaVoice 源码和 Python/Tcl/Tk 启动界面。Python 使用 PSF 许可，Tcl/Tk 保留发行版许可；构建时复制随附声明到 LICENSES。PyInstaller 6.16.0 的 bootloader 使用其 GPL 例外，详见 https://pyinstaller.org/en/stable/license.html 。
- sounddevice 0.5.3：MIT；PortAudio 另保留发行包版权。安装时由包源获取，不将环境整目录打包。
- uv 0.9.11：MIT / Apache-2.0；从 PyPI 官方发行文件下载并校验 SHA256，不修改系统 PATH。
- OpenAI tunnel-client 0.0.21：Apache-2.0，下载包附带 NOTICE、依赖许可和 SPDX 清单，完整保留。
- imageio-ffmpeg 包含 FFmpeg 二进制，安装后保留 Python 发行包中的许可与第三方声明；不随 LinguaVoice 源码和启动器重分发二进制。
- 基础模型与语言资料从列明来源下载，分别遵守 HuBERT、Chinese RoBERTa、G2PW、fastText、GPT-SoVITS 底模、NLTK 数据集条款。本项目的 MIT 不替代它们；不将这些下载内容包含在公开启动器内。

2026-11-24 核对 Kara251 模型仓库 README：微调权重 CC-BY-NC-4.0；禁止倒卖；参考音频和台词来自《蔚蓝档案》，遵循原权利方条款。来源 https://modelscope.cn/models/Kara251/kara-gsv-model 。许可 https://creativecommons.org/licenses/by-nc/4.0/deed.zh-hans 。已知完整包按 SHA256 记录作者、来源、许可和核查日期；未知包显示“许可未核实”，不会自动继承其他包的许可。

“已核对发布者声明”不表示已获得全部权利方的再分发授权。第三方角色权重、参考录音及私人参数配置不包含在公开安装器里。对于用户自己制作或另行取得授权的模型，也保留其原许可。本次 GitHub 发布只包含自有源码、默认声音资料和启动安装器；不代表官方插件目录上架，也不授予第三方声音的再分发权利。

## 默认声音与 Qwen

默认声音由 Qwen3-TTS-12Hz-1.7B-VoiceDesign 根据文字描述生成，未使用游戏角色原始录音。只公开用户确认的自定义参考录音和计算出的声音提示；它们不授予任何第三方角色权利。模型权重在安装时从官方仓库下载，不捆绑分发。

Qwen3-TTS Base 与 VoiceDesign 模型页面标注 Apache-2.0；Qwen 代码遵守原仓库许可。https://huggingface.co/Qwen/Qwen3-TTS-12Hz-0.6B-Base ，https://huggingface.co/Qwen/Qwen3-TTS-12Hz-1.7B-VoiceDesign 。faster-qwen3-tts 0.5.4 使用 MIT，依赖 qwen-tts-hf、Transformers 5.15.1 等，保留各发行包许可。GPT-SoVITS 使用独立依赖环境，Transformers 4.57.6。

qwen-tts-hf 0.1.1.post1 的包元数据声明 Apache-2.0，来源为 Qwen3-TTS 的 Transformers 5 兼容分支；不把该分支当作未经修改的官方主分支。
