"""Read-only provider diagnostics. Never prints keys, prompts, or provider response bodies."""
import json
import os
import sys
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import HTTPError

env=Path(__file__).resolve().parents[1]/'.env'
if env.exists():
    for line in env.read_text(encoding='utf-8-sig').splitlines():
        if '=' in line and not line.lstrip().startswith('#'):
            key,value=line.split('=',1)
            os.environ.setdefault(key.strip(),value.strip().strip('\"\''))

def check(provider,url,headers,body=None):
    try:
        req=Request(url,headers=headers,data=json.dumps(body).encode() if body else None)
        with urlopen(req,timeout=15) as response:
            data=json.load(response)
        result={'provider':provider,'reachable':True}
        if provider=='Gemini':
            names=[m['name'].split('/')[-1] for m in data.get('models',[]) if 'generateContent' in m.get('supportedGenerationMethods',[])]
            model=os.getenv('GEMINI_MODEL','gemini-3-flash-preview')
            result.update(configured_model=model,model_available=model in names,flash_models=[n for n in names if 'flash' in n][:8])
        elif provider=='GeminiGeneration':
            result['text_present']=any(p.get('text') for c in data.get('candidates',[]) for p in c.get('content',{}).get('parts',[]))
        else: result['results']=len(data.get('results',[]))
        print(json.dumps(result),flush=True)
    except HTTPError as exc:
        try:
            message=json.load(exc).get('error',{}).get('message','')
        except Exception:
            message='No structured error'
        for key in ('GOOGLE_API_KEY','TAVILY_API_KEY'):
            if os.getenv(key): message=message.replace(os.environ[key],'[redacted]')
        print(json.dumps({'provider':provider,'reachable':False,'http_status':exc.code,'message':message[:400]}),flush=True)
    except Exception as exc:
        print(json.dumps({'provider':provider,'reachable':False,'error_type':type(exc).__name__}),flush=True)

if __name__=='__main__':
    if len(sys.argv)>2 and sys.argv[1]=='--generate':
        model=sys.argv[2]
        check('GeminiGeneration',f'https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent',
              {'x-goog-api-key':os.getenv('GOOGLE_API_KEY',''),'Content-Type':'application/json'},
              {'contents':[{'parts':[{'text':'Reply with the word OK.'}]}],'generationConfig':{'maxOutputTokens':256}})
        sys.exit(0)
    check('Gemini','https://generativelanguage.googleapis.com/v1beta/models',{'x-goog-api-key':os.getenv('GOOGLE_API_KEY','')})
    check('Tavily','https://api.tavily.com/search',{'Authorization':'Bearer '+os.getenv('TAVILY_API_KEY',''),'Content-Type':'application/json'},
          {'query':'ICAR plant soil moisture irrigation guidance','max_results':2,'include_answer':False})
