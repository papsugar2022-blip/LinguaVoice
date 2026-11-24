"""Explicit inference profiles; original third-party weight files are never rewritten."""
from pathlib import Path


def apply_voice_profile(tts, name, base_weights):
    if name == 'original':
        return {'name': name, 'description': '使用该角色的原始权重'}
    if name not in {'cfm-base', 'cfm-base-50'}:
        raise ValueError(f'未知音色推理配置：{name}')
    if tts.configs.version != 'v5turbo':
        raise ValueError('此当前角色配置仅已验证于 V5Turbo，不能自动用于其他版本')
    from process_ckpt import get_sovits_version_from_path_fast, load_sovits_new
    base_weights = Path(base_weights)
    if get_sovits_version_from_path_fast(str(base_weights))[1:] != ['v5turbo', False]:
        raise ValueError('所选底模不是 V5Turbo 完整底模')
    checkpoint = load_sovits_new(str(base_weights))
    cfm_weights = {key[len('cfm.'):]: value for key, value in checkpoint['weight'].items()
                   if key.startswith('cfm.')}
    if not cfm_weights:
        raise ValueError('底模没有 CFM 参数，停止而不是回退到其他音色')
    # Strict matching prevents silently mixing incompatible architectures.
    tts.vits_model.cfm.load_state_dict(cfm_weights, strict=True)
    tts.vits_model.cfm.eval()
    if name == 'cfm-base-50':
        import torch
        live_weights = tts.vits_model.state_dict()
        # Reproduce J: base + 0.5 * (trained - base), excluding the restored CFM.
        with torch.no_grad():
            for key, current in live_weights.items():
                if key.startswith('cfm.'):
                    continue
                base_value = checkpoint['weight'][key]
                if base_value.shape != current.shape:
                    raise ValueError(f'J 配置的底模参数形状不匹配：{key}')
                base_value = base_value.to(device=current.device, dtype=current.dtype)
                if current.is_floating_point():
                    current.copy_(torch.lerp(base_value, current, 0.5))
                elif not torch.equal(base_value, current):
                    raise ValueError(f'J 配置遇到不一致的非浮点参数：{key}')
    return {
        'name': name,
        'description': ('J：CFM 使用底模，其余 SoVITS 参数按50%微调差量混合；当前角色 GPT 与参考不变，无后期降噪。'
                        if name == 'cfm-base-50' else
                        '仅恢复 V5Turbo 底模的 CFM 参数；保留当前角色 GPT、参考音频和其余 SoVITS 微调参数。无后期降噪。'),
        'cfm_finetune_scale': 0.0,
        'other_finetune_scale': 0.5 if name == 'cfm-base-50' else 1.0,
        'base_weights': str(base_weights),
        'original_weight_files_modified': False,
        'validation': ('用户最终选定 J，停止调音。未试听语言和文本不因此视为已通过。'
                       if name == 'cfm-base-50' else
                       'I 短句获认可，但较长问句喘气处仍有轻微异响；已由J替代为默认入口配置。'),
    }
