"""User-facing support facts and local readiness, separate from voice display names."""
import json
from pathlib import Path
from .model_versions import VERSIONS
from .storage import ROOT


def support_state(store):
    base = ROOT/'vendor/GPT-SoVITS/GPT_SoVITS/pretrained_models'
    evidence_file=ROOT/'private/verified-capabilities.json'
    try:evidence=json.loads(evidence_file.read_text(encoding='utf-8'))
    except (OSError,ValueError):evidence={}
    versions=[]
    for version in ('v5turbo',):
        missing=[p for p in VERSIONS[version]['files'] if not (base/p).is_file()]
        verified=bool(evidence.get('gpt-sovits/'+version))
        versions.append({'provider':'GPT-SoVITS','version':version,
            'status':'还需安装组件' if missing else ('本机已实测' if verified else '已接入，尚待真实合成验证'),
            'verified':verified and not missing,'missing_components':len(missing),
            'note':'使用该版本自己的底模和声码器，不混用其他版本。'})
    versions.append({'provider':'Qwen3-TTS','version':'0.6B Base',
        'status':'本机已实测' if evidence.get('qwen3-tts') else '已接入；安装器会实际生成声音后确认', 'verified':bool(evidence.get('qwen3-tts')),
        'note':'使用参考录音或已保存的声音提示；与 GPT-SoVITS 权重不是同一种格式。'})
    return {'versions':versions,
        'languages':[{'name':'中文','status':'GPT-SoVITS、Qwen 已有真实合成记录；本机状态见上表'},
            {'name':'英语','status':'Qwen 已真实生成；发音与英式口音仍需按音色试听'},
            {'name':'日语、韩语','status':'两个引擎接口可选，尚未逐项试听'},
            {'name':'粤语','status':'GPT-SoVITS 接口可选，尚未逐项试听'},
            {'name':'德语、法语、俄语、葡萄牙语、西班牙语、意大利语','status':'Qwen 接口可选，尚未逐项试听'}],
        'imports':[{'format':'完整 GPT-SoVITS 角色 ZIP','status':'已支持',
            'note':'目前要求 config.json、models/gpt.ckpt、models/sovits.pth、参考录音及 .list 台词标注。参考录音选 3～10 秒；ZIP 最大 1 GB，展开最大 3 GB。'},
            {'format':'普通单角色模型 ZIP','status':'支持自动整理',
             'note':'需要配对权重、参考音频及台词；同系列多轮权重选最高轮次并显示文件。多个角色或配对不明时拒绝。单独权重不够。'},
            {'format':'Qwen3-TTS 参考声音 ZIP','status':'已接入',
             'note':'voice.json 清单加3～30秒参考录音和准确台词，使用已安装的 Qwen 基础模型。无需提供训练权重。'},
            {'format':'LinguaVoice 设置备份 .json','status':'更多设置 → 恢复设置备份',
             'note':'恢复设置，不会安装声音；对应音色需先在本机存在。'},
            {'format':'只有录音、RVC 模型、训练数据合集','status':'不能直接当作 GPT-SoVITS 声音安装',
             'note':'这些资料用途不同，不会改名冒充兼容。'}],
        'features':['AI 根据聊天和教学语境选择说哪些话；文字和语音可以不同，也可以不说话',
            '代码、公式、标题和动作描写通常只显示；语音讲思路，可用“这个函数”“这个式子”等指代',
            '合成语速 0.6～1.5 倍；暂停、继续、停止和重播；可关闭自动播放',
            '电脑后台播放；设置网页可以关闭','Windows 登录自动准备和网络恢复重连','角色绑定、设置备份、模型来源及许可说明'],
        'limits':['目前验证环境：Windows、NVIDIA 显卡；其他系统和硬件尚未验收',
            '电脑需要开机；本地声音从这台电脑输出；网页播放首次可能需要点击开启',
            'ChatGPT 是否提供插件工具取决于账号、客户端和该轮对话',
            '免费账号、手机端和全新电脑安装仍需分别验收','未列出的模型版本不自动视为兼容；不会用其他声音冒充'],
        'voices':[{'id':v['id'],'name':v['name'],'provider':v['provider'],'version':v['version']} for v in store.snapshot()['voices'].values() if v['provider']=='qwen3-tts' or v['version'] in VERSIONS]}
