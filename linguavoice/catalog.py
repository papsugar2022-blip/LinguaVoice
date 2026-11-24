"""Optional user-owned catalog; no third-party character registry is bundled."""
import json
from pathlib import Path

def catalog_items():
    path=Path(__file__).resolve().parents[1]/'private/voice-catalog.json'
    return json.loads(path.read_text(encoding='utf-8')) if path.is_file() else []

def provenance(digest):
    item=next((x for x in catalog_items() if x.get('sha256')==digest),None)
    if not item:
        return {'author':'','source_url':'','license':'许可未核实。请确认有权使用；导入不代表获得再分发授权。',
                'license_status':'user_confirmation_only','redistribution_reviewed':False}
    return {**{k:item[k] for k in ('author','source_url','license','license_url') if k in item},
            'license_status':'publisher_statement_checked','checked_on':'2026-11-24','redistribution_reviewed':False}
