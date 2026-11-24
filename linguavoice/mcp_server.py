"""MCP adapter. It forwards selected utterances, never reads a chat transcript."""
import argparse
import asyncio
import base64
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from typing import Literal
from mcp.server import MCPServer
from mcp import types
from .storage import ROOT
from .instructions import INSTRUCTIONS

BASE = 'http://127.0.0.1:8769'
TOKEN_FILE = ROOT/'private/console/access-token.txt'


def ensure_service():
    try:
        with urllib.request.urlopen(BASE+'/health', timeout=2) as r:
            if json.load(r).get('service') == 'linguavoice':
                return
        raise RuntimeError('本机8769端口不是LinguaVoice服务')
    except urllib.error.URLError:
        (ROOT/'private').mkdir(exist_ok=True)
        with (ROOT/'private/console-background.log').open('ab') as log:
            subprocess.Popen([sys.executable, '-m', 'linguavoice.server'], cwd=ROOT,
                stdout=log, stderr=log, creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
        for _ in range(40):
            time.sleep(.25)
            try:
                with urllib.request.urlopen(BASE+'/health', timeout=1) as r:
                    if json.load(r).get('service') == 'linguavoice':
                        return
            except urllib.error.URLError:
                pass
        raise RuntimeError('本机语音控制台未能启动，请运行“启动控制台.cmd”')


def request(path, body=None, raw=False):
    token = TOKEN_FILE.read_text(encoding='ascii').strip()
    data = json.dumps(body, ensure_ascii=False).encode('utf-8') if body is not None else None
    req = urllib.request.Request(BASE+path, data=data,
        headers={'Authorization': 'Bearer '+token, 'Content-Type': 'application/json'})
    try:
        with urllib.request.urlopen(req, timeout=35) as response:
            content = response.read()
            return content if raw else json.loads(content)
    except urllib.error.HTTPError as exc:
        try:
            message = json.loads(exc.read()).get('error', str(exc))
        except Exception:
            message = str(exc)
        raise ValueError(message) from None


async def resource_diagnostics(ctx, call_next):
    # Opt-in local diagnostics: record only method/status, never arguments or audio.
    enabled = (ROOT/'private/replay-diagnostics.enabled').is_file()
    watched = ctx.method in {'resources/list', 'resources/templates/list', 'resources/read', 'tools/list'}
    if not (enabled and watched):
        return await call_next(ctx)
    outcome = 'ok'
    try:
        return await call_next(ctx)
    except Exception:
        outcome = 'error'
        raise
    finally:
        try:
            with (ROOT/'private/replay-resource-trace.jsonl').open('a', encoding='utf-8') as log:
                log.write(json.dumps({'time': time.time(), 'pid': os.getpid(),
                                      'method': ctx.method, 'outcome': outcome})+'\n')
        except OSError:
            pass


server = MCPServer('LinguaVoice', version='0.3.4', instructions=INSTRUCTIONS,
                   middleware=[resource_diagnostics])
REPLAY_UI = 'ui://linguavoice/replay-v3.html'
CHATGPT_REPLAY_UI = 'ui://linguavoice/replay-chatgpt-v3.html'
CARD_META = {'ui': {'resourceUri': REPLAY_UI}, 'openai/outputTemplate': CHATGPT_REPLAY_UI}


@server.resource(REPLAY_UI, mime_type='text/html;profile=mcp-app',
                 meta={'ui': {'prefersBorder': True}})
@server.resource('ui://linguavoice/replay-v2.html', mime_type='text/html;profile=mcp-app',
                 meta={'ui': {'prefersBorder': True}})
@server.resource('ui://linguavoice/replay-v1.html', mime_type='text/html;profile=mcp-app',
                 meta={'ui': {'prefersBorder': True}})
def replay_card() -> str:
    return (ROOT/'linguavoice/web/replay.html').read_text(encoding='utf-8')


@server.resource(CHATGPT_REPLAY_UI, mime_type='text/html+skybridge',
                 meta={'openai/widgetPrefersBorder': True,
                       'openai/widgetAccessible': True})
@server.resource('ui://linguavoice/replay-chatgpt-v2.html', mime_type='text/html+skybridge',
                 meta={'openai/widgetPrefersBorder': True,
                       'openai/widgetAccessible': True})
def chatgpt_replay_card() -> str:
    return replay_card().replace('const legacy=false;', 'const legacy=true;')
READ = types.ToolAnnotations(readOnlyHint=True, destructiveHint=False, openWorldHint=False)
WRITE = types.ToolAnnotations(readOnlyHint=False, destructiveHint=False, openWorldHint=False)


@server.tool(annotations=READ)
async def linguavoice_list_voices() -> dict:
    """查看可用私人音色、角色绑定、默认语速与播放器状态。先确认可用音色；不要擅自更换角色。"""
    await asyncio.to_thread(ensure_service)
    data = await asyncio.to_thread(request, '/api/state')
    return {k:data[k] for k in ('voices','settings','playback','engine_state')}


async def audio_result(job_id):
    job = await asyncio.to_thread(request, '/api/jobs/'+job_id)
    if job['status'] != 'ready':
        return types.CallToolResult(content=[types.TextContent(text=json.dumps(job,ensure_ascii=False))],
                                    structuredContent=job, isError=job['status']=='error')
    audio = await asyncio.to_thread(request, job['audio_path'], None, True)
    if len(audio) > 6*1024*1024:
        return types.CallToolResult(content=[types.TextContent(text='语音已生成，可在本机控制台播放；单段过长，无法安全嵌入MCP结果。后续请按语义缩短分段。')], structuredContent=job)
    # Do not return a hidden listening transcript as model-visible text.
    metadata = {k:job[k] for k in ('id','status','voice_name','language','speed','hide_text','seconds','playback_target') if k in job}
    metadata.update({k:job[k] for k in ('playback_status','playback_error') if k in job})
    return types.CallToolResult(content=[types.TextContent(text='已合成私人音色语音；实际播放取决于客户端或本机播放器。'),
        types.AudioContent(data=base64.b64encode(audio).decode('ascii'), mimeType='audio/wav')], structuredContent=metadata)


@server.tool(annotations=WRITE, structured_output=False, meta=CARD_META)
async def linguavoice_speak(text: str, voice_id: str | None = None, role: str | None = None,
        language: Literal['zh','en','ja','ko','yue','de','fr','ru','pt','es','it'] = 'zh', speed: float | None = None,
        hide_text: bool = False) -> types.CallToolResult:
    """角色主动说话：当前对话启用语音后，后续没有@或朗读指令也继续按语境判断。普通聊天须先向用户显示实际角色台词，再调用本工具，否则声音可能早于正文；听力隐藏原文除外。自行选出角色真正要对用户说的完整台词（1～400字），用于日常聊天、关心、提问、自然讲解或教学示范，无需用户逐句指定。动作描写、旁白、标题和格式通常只留在文字里；按意义判断，不按引号或语言硬切。可以不发声，也可按语义分段多次调用；不要机械全文朗读或重复提交。一次只用一个已绑定角色的音色，未绑定不冒用。speed为0.6～1.5，省略使用控制台默认值。hide_text用于听力折叠原文，文字回复不要泄露答案。返回真实音频不代表已经播放。"""
    await asyncio.to_thread(ensure_service)
    job = await asyncio.to_thread(request, '/api/speak', {'text':text,'voice_id':voice_id,'role':role,
        'language':language,'speed':speed,'hide_text':hide_text,'source':'chatgpt-mcp'})
    deadline = time.monotonic()+85
    while time.monotonic()<deadline:
        job = await asyncio.to_thread(request, '/api/jobs/'+job['id'])
        if job['status'] in {'ready','error','cancelled'}:
            return await audio_result(job['id'])
        await asyncio.sleep(.5)
    return types.CallToolResult(content=[types.TextContent(text='仍在合成，请稍后用linguavoice_get_audio读取此任务，不要重复提交。')],
        structuredContent={'id':job['id'],'status':job['status']})


@server.tool(annotations=READ, structured_output=False, meta=CARD_META)
async def linguavoice_get_audio(job_id: str) -> types.CallToolResult:
    """获取已提交任务的状态或音频，不会重新合成或触发电脑重播。要求重播时请用 linguavoice_replay。"""
    if len(job_id)!=32 or any(c not in '0123456789abcdef' for c in job_id):
        raise ValueError('无效任务ID')
    await asyncio.to_thread(ensure_service)
    return await audio_result(job_id)


@server.tool(annotations=READ, structured_output=False, meta=CARD_META)
async def linguavoice_show_player(job_id: str) -> types.CallToolResult:
    """显示已有语音的“再听一遍”按钮。用户说没找到按钮、要播放卡片时调用本工具，将卡片呈现给用户，不要只口头说有按钮。job_id 取本对话此前语音返回的 id。只显示播放器，不重新合成、不自动重播、不返回整段音频。"""
    if len(job_id) != 32 or any(c not in '0123456789abcdef' for c in job_id):
        raise ValueError('无效任务ID；请使用本对话此前语音返回的 id')
    await asyncio.to_thread(ensure_service)
    job = await asyncio.to_thread(request, '/api/jobs/' + job_id)
    metadata = {k: job[k] for k in ('id', 'status', 'voice_name', 'seconds') if k in job}
    return types.CallToolResult(
        content=[types.TextContent(text='已返回这段录音的播放卡片。请向用户呈现工具界面；若当前宿主未显示卡片，应如实说明，不能把已返回说成用户已看到。')],
        structuredContent=metadata, isError=job['status'] in {'error', 'cancelled'})


@server.tool(annotations=WRITE, structured_output=False, meta={'ui': {'visibility': ['model', 'app']}, 'openai/widgetAccessible': True})
async def linguavoice_replay(job_id: str) -> types.CallToolResult:
    """用户说再听一遍、重播上一句时，重播当前对话中对应的已有录音，不重新合成。job_id 必须取自本对话之前 speak/get_audio 的返回 id，不要猜测或使用其他对话的最近任务。无需用户填写编号。后台播放需已开启；返回 queued 只代表已排队，不代表已播放完成。"""
    if len(job_id)!=32 or any(c not in '0123456789abcdef' for c in job_id):
        raise ValueError('无效任务ID；请使用本对话此前语音返回的 id')
    await asyncio.to_thread(ensure_service)
    result = await asyncio.to_thread(request, '/api/replay', {'job_id':job_id})
    return types.CallToolResult(content=[types.TextContent(text='已请求重播这段录音，无需重新合成。')], structuredContent=result)


@server.tool(annotations=WRITE)
async def linguavoice_playback(action: Literal['pause','resume','stop']) -> dict:
    """用户要求时，向电脑后台播放器及网页播放器发送暂停、继续或停止命令。后台模式无需打开设置网页。不能控制ChatGPT原生朗读按钮。"""
    await asyncio.to_thread(ensure_service)
    return await asyncio.to_thread(request, '/api/control', {'action':action})


@server.tool(annotations=WRITE)
async def linguavoice_open_console() -> dict:
    """用户需要设置语速、音色、角色绑定、自动播放或导入模型时，打开本机控制台。不会更改声音设置。仅在运行后端的电脑上打开。"""
    await asyncio.to_thread(ensure_service)
    import webbrowser
    token = TOKEN_FILE.read_text(encoding='ascii').strip()
    await asyncio.to_thread(webbrowser.open, BASE+'/#token='+token)
    return {'opened':True,'console':BASE,'note':'本机控制台；手机不会因此自动打开或获得本机音频访问权限'}


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--transport',choices=['stdio','streamable-http'],default='stdio')
    args=parser.parse_args()
    if args.transport=='stdio':
        server.run()
    else:
        # Private loopback endpoint only. Use stdio through a secure tunnel for ChatGPT.
        server.run(transport='streamable-http',host='127.0.0.1',port=8770,json_response=True,stateless_http=True)


if __name__=='__main__':
    main()
