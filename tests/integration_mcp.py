"""Real GPU/MCP check; writes only compact metrics, never audio base64 or tokens."""
import asyncio
import io
import json
import sys
from pathlib import Path
import soundfile as sf
from mcp import Client
from mcp.client.stdio import StdioServerParameters

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from linguavoice.mcp_server import request

async def main():
    results=[]
    async with Client(StdioServerParameters(command=sys.executable,args=['-m','linguavoice.mcp_server'],cwd=ROOT),read_timeout_seconds=115) as client:
        discovery=await client.list_tools()
        print('Tools: '+', '.join(t.name for t in discovery.tools),flush=True)
        voices=await client.call_tool('linguavoice_list_voices',{})
        assert not voices.is_error, voices
        for speed in [1.0,0.8]:
            r=await client.call_tool('linguavoice_speak',{'text':'老师，今天想从哪里开始呢？我们可以慢慢来，不用着急。','language':'zh','speed':speed,'hide_text':True})
            assert not r.is_error, [c.text for c in r.content if c.type=='text']
            for _ in range(40):
                if r.structured_content.get('status') == 'ready': break
                await asyncio.sleep(3)
                r=await client.call_tool('linguavoice_get_audio',{'job_id':r.structured_content['id']})
                assert not r.is_error, r.structured_content
            assert not any(c.type=='audio' for c in r.content)
            assert len(r.model_dump_json().encode()) < 1500
            jid=r.structured_content['id']
            job=request('/api/jobs/'+jid)
            audio=request(job['audio_path'],None,True)
            pcm,sr=sf.read(io.BytesIO(audio))
            assert len(pcm)>sr and abs(pcm).max()>0
            assert 'text' not in r.structured_content
            item={'speed':speed,'seconds':len(pcm)/sr,'sample_rate':sr,'job_id':r.structured_content['id'],'real_audio_bytes':len(audio)}
            results.append(item)
            print(json.dumps(item),flush=True)
        assert results[1]['seconds']>results[0]['seconds']*1.1, 'Slow synthesis did not lengthen speech'
    (ROOT/'private/console/integration-results.json').write_text(json.dumps(results,indent=2),encoding='utf-8')
    print('PASS: real MCP discovery, voice selection, default voice synthesis, speed, hidden transcript, bounded MCP result and authenticated local WAV.',flush=True)

if __name__=='__main__':asyncio.run(main())
