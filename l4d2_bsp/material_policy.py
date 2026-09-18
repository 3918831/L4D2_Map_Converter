"""Optional static-prop reflections with private resources inside BSP/VPK.

No game-install writes, global cvars, same-name material overrides or geometry
edits. The initial resource catalog is bounded; it is not a wetness classifier.
"""
import hashlib
import io
import re
import struct
import zipfile
from decimal import Decimal, InvalidOperation

from .binary import BspFile
from .inspect import _game_lump_inventory
from .material_models import material_table, clone_model, check_vtx_materials
from .model_lighting import ResourceSnapshot, _prop_models, model_layout
from .native import _game_entries, _pak_entries, _safe_name


POLICIES=('preserve','catalogued-static-reflections-v1')
CATALOG={
    'materials/models/props_mill/pipeset32d.vmt',
    'materials/models/props_mill/boiler_01.vmt',
    'materials/models/props_mill/tank_large.vmt',
    'materials/models/props/de_train/de_train_horizontalcoolingtank.vmt',
}
_TOKEN=re.compile(r'//[^\r\n]*|"[^"\r\n]*"|[{}]|[^\s{}"]+')


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def soften_material(data):
    """Edit two explicit scalar responses only; reject ambiguous VMT syntax."""
    text=data.decode('ascii');tokens=[];end=0
    for m in _TOKEN.finditer(text):
        if text[end:m.start()].strip():raise ValueError('Unsupported VMT syntax')
        end=m.end()
        if not m[0].startswith('//'):tokens.append((m[0].strip('"'),m.start(),m.end()))
    if text[end:].strip() or len(tokens)<3 or tokens[0][0].lower()!='vertexlitgeneric':
        raise ValueError('Only flat VertexLitGeneric materials are supported')
    if tokens[1][0]!='{' or tokens[-1][0]!='}' or (len(tokens)-3)%2:
        raise ValueError('Unsupported VMT structure')
    fields={}
    for i in range(2,len(tokens)-1,2):
        key,value=tokens[i][0].lower(),tokens[i+1]
        if key in fields or key in ('{','}') or value[0] in ('{','}'):
            raise ValueError('Duplicate or nested VMT fields require review')
        fields[key]=value
    changes=[]

    def scale(key,count):
        value,start,end=fields[key]
        if count==3:
            if not (value.startswith('[') and value.endswith(']')):raise ValueError('Unsupported VMT vector')
            value=value[1:-1]
        try:numbers=[Decimal(n) for n in value.split()]
        except InvalidOperation as exc:raise ValueError('Invalid VMT numeric value') from exc
        if len(numbers)!=count or any(not n.is_finite() or not 0<=n<=16 for n in numbers):
            raise ValueError('VMT numeric value outside supported bounds')
        reduced=' '.join(format(n*Decimal('.5'),'f') for n in numbers)
        if count==3:reduced='['+reduced+']'
        changes.append(dict(key=key,before=fields[key][0],after=reduced,start=start,end=end))

    if fields.get('$envmap',('',))[0].lower()=='env_cubemap' and '$envmaptint' in fields:
        scale('$envmaptint',3)
    if fields.get('$phong',('',))[0]=='1' and '$phongboost' in fields:
        scale('$phongboost',1)
    if not changes:raise ValueError('No supported explicit reflection strength')
    for item in sorted(changes,key=lambda x:x['start'],reverse=True):
        text=text[:item['start']]+'"'+item['after']+'"'+text[item['end']:]
    return text.encode('ascii'),[{k:x[k] for k in ('key','before','after')} for x in changes]


def _resolve_material(snapshot,names,dirs):
    resolved=[]
    for name in names:
        found=None
        for directory in dirs or ['']:
            relative=(directory.replace('\\','/')+name.replace('\\','/')).lower()
            path='materials/'+relative+'.vmt';_safe_name(path)
            try:data=snapshot.read(path)
            except ValueError as exc:
                if str(exc).startswith('Missing model resource:'):continue
                raise
            found=(path,data);break
        if found is None:raise ValueError(f'Missing model material: {name}')
        resolved.append(found)
    return resolved


def apply_material_policy(data,roots,*,policy='preserve'):
    if policy not in POLICIES:raise ValueError('Unsupported material policy')
    audit=dict(policy=policy,source_sha256=_sha(data),model_copies=[],material_changes=[],skipped=[],
               prop_instances_unchanged=True,entities_unchanged=True,original_pak_payloads_unchanged=True)
    if policy=='preserve':return data,audit
    parsed=BspFile.parse(data);entries=_pak_entries(parsed.lump_bytes(40));snapshot=ResourceSnapshot(roots,entries)
    # _prop_models validates the complete sprp instance layout before any edit.
    models=sorted(set(_prop_models(parsed)))
    namespace=_sha(data+policy.encode())[:20];copies={};mapping={}
    for number,model in enumerate(models):
        try:
            mdl=snapshot.read(model);names,dirs=material_table(mdl)
            # Avoid requiring unrelated materials merely to prove catalog absence.
            candidates={'materials/'+(d.replace('\\','/')+n.replace('\\','/')).lower()+'.vmt'
                        for d in dirs or [''] for n in names}
            if not candidates & CATALOG:continue
            resolved=_resolve_material(snapshot,names,dirs);changed={};details=[]
            for i,(path,raw) in enumerate(resolved):
                if path not in CATALOG:continue
                replacement,fields=soften_material(raw);changed[i]=replacement
                details.append(dict(source=path,fields=fields))
            if not changed:continue
            private=f'lmc/{namespace}/m{number}/'
            destination='models/'+private+'model.mdl'
            clone=clone_model(mdl,destination,private)
            vvd=snapshot.read(model[:-4]+'.vvd');vtx=snapshot.read(model[:-4]+'.dx90.vtx')
            phy=snapshot.read(model[:-4]+'.phy')
            check_vtx_materials(vtx)
            layout=model_layout(mdl,vvd,vtx)
            if model_layout(clone,vvd,vtx)!=layout:raise ValueError('Cloned model layout changed')
            if len(phy)<16 or struct.unpack_from('<I',phy,12)[0]!=layout[0]:
                raise ValueError('Physics/MDL checksum mismatch')
            family={destination:clone,destination[:-4]+'.vvd':vvd,
                    destination[:-4]+'.dx90.vtx':vtx,destination[:-4]+'.phy':phy}
            for i,(source,raw) in enumerate(resolved):
                target='materials/'+private+names[i].replace('\\','/').lower()+'.vmt'
                _safe_name(target);value=changed.get(i,raw)
                if target in family and family[target]!=value:raise ValueError('Ambiguous private material name')
                family[target]=value
            if any(n in entries or n in copies for n in family):raise ValueError('Private resource collision')
        except (ValueError,UnicodeError,struct.error) as exc:
            audit['skipped'].append(dict(model=model,reason=str(exc)));continue
        copies.update(family);mapping[model]=destination
        audit['model_copies'].append(dict(source=model,destination=destination,checksum=layout[0]))
        audit['material_changes'].extend(details)
    audit['source_inventory']=snapshot.inventory()
    if not copies:
        audit['output_sha256']=_sha(data)
        return data,audit
    output=bytearray(data)
    sprp=next(x for x in _game_lump_inventory(parsed) if x['id']=='sprp')
    start=sprp['offset'];count=struct.unpack_from('<i',data,start)[0]
    for i in range(count):
        pos=start+4+i*128;name=data[pos:pos+128].split(b'\0')[0].decode('ascii').replace('\\','/').lower()
        if name in mapping:output[pos:pos+128]=mapping[name].encode().ljust(128,b'\0')
    buffer=io.BytesIO()
    with zipfile.ZipFile(buffer,'w',compression=zipfile.ZIP_STORED) as archive:
        for name,value in sorted((entries|copies).items()):
            archive.writestr(zipfile.ZipInfo(name,date_time=(1980,1,1,0,0,0)),value)
    payload=buffer.getvalue();output.extend(b'\0'*(-len(output)%4));offset=len(output);output.extend(payload)
    struct.pack_into('<ii',output,8+40*16+4,offset,len(payload))
    final=BspFile.parse(bytes(output));oldgame=_game_entries(parsed);newgame=_game_entries(final)
    if any(parsed.lump_bytes(i)!=final.lump_bytes(i) for i in range(64) if i not in (35,40)):
        raise ValueError('Material policy changed unrelated BSP lump')
    if oldgame.keys()!=newgame.keys():raise ValueError('Material policy changed game directory')
    for name,entry in oldgame.items():
        if name!='sprp' and entry!=newgame[name]:raise ValueError('Material policy changed other game payload')
    if oldgame['sprp'][2][4+count*128:]!=newgame['sprp'][2][4+count*128:]:
        raise ValueError('Material policy changed static prop instances')
    finalentries=_pak_entries(final.lump_bytes(40))
    if finalentries!=entries|copies:raise ValueError('Material policy PAK verification failed')
    audit['added_resources']=[dict(name=n,sha256=_sha(v),size=len(v)) for n,v in sorted(copies.items())]
    audit['output_sha256']=_sha(bytes(output))
    return bytes(output),audit
