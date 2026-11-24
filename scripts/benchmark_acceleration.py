"""Real inference with the installed acceleration dependency, preserving the selected voice."""
import argparse, contextlib, json
from pathlib import Path
from linguavoice.engine import GPTSoVITSBackend
from linguavoice.storage import ROOT, Store

def main():
    p=argparse.ArgumentParser();p.add_argument('destination');p.add_argument('--voice-id',required=True);args=p.parse_args()
    out=Path(args.destination);out.mkdir(parents=True,exist_ok=True)
    backend=GPTSoVITSBackend();voice=Store().voice(args.voice_id);results=[]
    if voice['provider']!='gpt-sovits':raise ValueError('This benchmark requires a GPT-SoVITS voice')
    text='老师，今天辛苦了。先坐下来休息一会儿吧。我们可以聊聊今天发生的事情，也可以一起练习几句简单的英语。不用急着把所有事情都做好，我会陪着你，按你觉得舒服的节奏慢慢来。'
    with (ROOT/'private/acceleration-benchmark.log').open('w',encoding='utf-8') as log:
        with contextlib.redirect_stdout(log),contextlib.redirect_stderr(log):
            warm=backend.synthesize(voice,'老师，我们开始吧。','zh',1.0,ROOT/'private/latency-warmup.wav')
        import torch
        print('Warmup finished',flush=True)
        for trial in [1,2]:
            with contextlib.redirect_stdout(log),contextlib.redirect_stderr(log):
                info=backend.synthesize(voice,text,'zh',1.0,out/f'accelerated-{trial}.wav')
            accel=backend.tts.t2s_accel
            info.update(backend=accel.backend if accel else None,graph_enabled=accel.graph_enabled if accel else False,
                        matmul_precision=torch.backends.cuda.matmul.fp32_precision,
                        cudnn_precision=torch.backends.cudnn.fp32_precision)
            results.append(info);print(json.dumps(info,ensure_ascii=False),flush=True)
    (out/'latency.json').write_text(json.dumps({'text':text,'voice':voice['id'],'warmup':warm,'results':results},ensure_ascii=False,indent=2),encoding='utf-8')
    backend.unload()

if __name__=='__main__':main()
