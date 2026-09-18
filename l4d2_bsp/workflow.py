"""Configurable offline conversion with manual or automatic native capture."""
import argparse
from datetime import datetime
import hashlib
import io
import json
from pathlib import Path
import re
import subprocess
import sys
import uuid
import zipfile

from .binary import BspFile
from . import __version__
from .configuration import GENERIC_CONVERSIONS, MAPS, file_hash, input_inventory, load_config, verify_inventory, verify_discovery
from .inspect import inspect_bytes


def write_json(path, value):
    path = Path(path)
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str) + '\n', encoding='utf-8')
    temporary.replace(path)


def tracked(path):
    return {'path': str(Path(path).resolve()), 'sha256': file_hash(path)}


def addon_info(map_name, phase, *, preset_id=None):
    if (not isinstance(map_name, str) or not re.fullmatch(r'[a-z0-9_-]{1,64}', map_name)
            or phase not in ('offline', 'final')):
        raise ValueError('Unsupported addon metadata identity')
    if preset_id is not None and not re.fullmatch('[a-z0-9-]+', preset_id):
        raise ValueError('Invalid preset metadata identity')
    style = preset_id or 'C5 global style'
    return (f'"AddonInfo"\n{{\n "addonSteamAppID" "550"\n "addontitle" "Map Converter {map_name} {phase}"\n'
            f' "addonversion" "{__version__}"\n "addonauthor" "L4D2 Map Converter"\n'
            f' "addonDescription" "{style}; {phase} HDR conversion. User runtime validation required."\n}}\n').encode('ascii')


def run_preset(report):
    """Read the exact preset used for this run, including capture controls."""
    metadata = report.get('preset')
    if not metadata:
        if report.get('config', {}).get('preset'):
            raise ValueError('Missing run preset snapshot metadata; use a new run')
        return None
    from .presets import parse_preset
    path = Path(metadata['snapshot_path'])
    preset = parse_preset(path.read_bytes(), source_path=path)
    if (preset.id != metadata['id'] or preset.sha256 != metadata['sha256']
            or preset.id != report['config'].get('preset')):
        raise ValueError('Run preset snapshot changed; use a new run')
    return preset


def run_capture_tonemap(report):
    """Use the conversion's audited controller for both capture flows."""
    cfg = report.get('config', {})
    preflight = report.get('preflight', {})
    modes_metadata = preflight.get('mode_styles', {})
    all_audits = [preflight.get('base_style'), *(modes_metadata.values() if isinstance(modes_metadata, dict) else [])]
    identities = [cfg.get('conversion'), cfg.get('profile'), report.get('profile'),
                  preflight.get('conversion'), preflight.get('profile')]
    for audit in all_audits:
        if isinstance(audit, dict):
            identities.append(audit.get('conversion'))
            if isinstance(audit.get('plan'), dict):
                identities.append(audit['plan'].get('conversion'))
    generic_marker = any(isinstance(value, str) and value.startswith('generic-replace-') for value in identities)
    if (cfg.get('conversion') is not None or generic_marker) and cfg.get('conversion') not in GENERIC_CONVERSIONS:
        raise ValueError('Missing or unsupported generic conversion rule; use a new run')
    if any(value in ('generic-replace-v2', 'generic-replace-v3') for value in identities) and (
            cfg.get('profile') != cfg.get('conversion')
            or any(isinstance(value, str) and value.startswith('generic-replace-') and value != cfg.get('conversion')
                   for value in identities)):
        raise ValueError('Generic conversion rule identity mismatch; use a new run')
    if cfg.get('conversion') not in GENERIC_CONVERSIONS:
        return 'tonemap_global'
    modes = cfg.get('mode_lmps')
    discovered = cfg.get('discovery', {}).get('mode_lmps')
    audits = preflight.get('mode_styles')
    if (not isinstance(modes, dict) or not isinstance(discovered, dict) or not isinstance(audits, dict)
            or not set(modes) <= set('hls') or set(audits) != set(modes)
            or set(discovered) != {mode + '_0' for mode in modes}):
        raise ValueError('Generic mode audit/config/discovery inventory mismatch; use a new run')

    def capture_name(audit):
        if cfg.get('conversion') in ('generic-replace-v2', 'generic-replace-v3') and (
                not isinstance(audit, dict) or not isinstance(audit.get('plan'), dict)
                or audit['plan'].get('conversion') != cfg['conversion']
                or audit.get('conversion') != cfg['conversion']):
            raise ValueError('Generic conversion rule/audit mismatch; use a new run')
        name = audit.get('capture_tonemap') if isinstance(audit, dict) else None
        plan = audit.get('plan') if isinstance(audit, dict) else None
        if not isinstance(name, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', name):
            raise ValueError('Missing or invalid generic capture tonemap metadata; use a new run')
        if not isinstance(plan, dict) or plan.get('capture_tonemap') != name:
            raise ValueError('Generic capture tonemap differs from conversion plan; use a new run')
        return name

    name = capture_name(preflight.get('base_style'))
    for audit in audits.values():
        if capture_name(audit) != name:
            raise ValueError('Generic capture tonemap differs between BSP and mode patches')
    return name


def load_run(root):
    root = Path(root).resolve()
    result = json.loads((root / 'run.json').read_text(encoding='utf-8'))
    if result.get('schema_version') != 1:
        raise ValueError('Unsupported run manifest version')
    verify_inventory(result['input_inventory'])
    verify_inventory(result.get('tracked_outputs', []))
    run_preset(result)
    run_capture_tonemap(result)
    if result.get('config', {}).get('conversion') in GENERIC_CONVERSIONS:
        verify_discovery(result['config'])
    if result.get('model_resource_inventory'):
        from .model_lighting import verify_resource_inventory
        from .native import _pak_entries
        snapshot = BspFile.parse((root/'bake/input.bsp.snapshot').read_bytes())
        verify_resource_inventory(result['config']['resource_roots'],
                                  _pak_entries(snapshot.lump_bytes(40)), result['model_resource_inventory'])
    verify_material_run(result, root)
    return result


def verify_material_run(report, root):
    """Verify original material inputs and bind the opt-in prepared baseline."""
    cfg=report.get('config',{});audit=report.get('preflight',{}).get('material_policy')
    policy=cfg.get('material_policy','preserve')
    if audit is None and policy=='preserve':return
    if not isinstance(audit,dict) or policy!=audit.get('policy') or policy!='catalogued-static-reflections-v1':
        raise ValueError('Material policy configuration/audit mismatch')
    from .model_lighting import verify_resource_inventory
    from .native import _pak_entries
    raw=(Path(root)/'bake/input.bsp.snapshot').read_bytes()
    if hashlib.sha256(raw).hexdigest()!=audit['output_sha256']:
        raise ValueError('Material policy prepared BSP identity mismatch')
    verify_resource_inventory(cfg['resource_roots'],_pak_entries(BspFile.parse(raw).lump_bytes(40)),audit['source_inventory'])


def status_summary(report):
    summary = {key: report.get(key) for key in ('run_id', 'map_name', 'profile', 'atmosphere_policy', 'status', 'error', 'last_capture_import_error')} | {
        'runtime_accepted': False,
        'acceptance_note': 'Build/capture import verification is not user runtime acceptance. Record your observations separately.'}
    for key in ('offline_package', 'final_package'):
        package = report.get(key)
        summary[key] = None if not package else {k: package.get(k) for k in ('vpk', 'sha256', 'native_extract_verified')} | {'file_count': len(package.get('files', []))}
    return summary


def check(config_path, *, progress=None):
    from .profiles import transfer_style
    from .resources import lookup_resources
    notify = progress if progress is not None else lambda stage: None
    notify("configuration_and_inputs")
    cfg = load_config(config_path)
    preset = None
    if cfg.get('preset'):
        from .presets import load_preset
        preset = load_preset(cfg['preset'])
    notify('input_format')
    source = cfg['source_bsp'].read_bytes()
    donor = preset if preset else cfg['reference_bsp'].read_bytes()
    inspection = inspect_bytes(source)
    if any(key.endswith('_error') for key in inspection):
        raise ValueError(f'Input BSP failed structural inspection: {inspection}')
    notify("conversion_plan")
    modes, mode_audits = {}, {}
    generic = cfg.get('conversion') in GENERIC_CONVERSIONS
    if generic:
        from .generic_conversion import transfer_generic
        rule_options = {'rule': cfg['conversion']} if cfg['conversion'] != 'generic-replace-v1' else {}
        output, audit = transfer_generic(source, preset, **rule_options)
        for key, path in cfg['mode_lmps'].items():
            modes[key], mode_audits[key] = transfer_generic(path.read_bytes(), preset, kind='lmp', **rule_options)
        run_capture_tonemap({'config': cfg, 'preflight': {'base_style': audit, 'mode_styles': mode_audits}})
    else:
        output, audit = transfer_style(source, donor, profile=cfg['profile'], atmosphere_policy=cfg['atmosphere_policy'])
        for key, path in cfg['mode_lmps'].items():
            modes[key], mode_audits[key] = transfer_style(path.read_bytes(), preset if preset else cfg['reference_lmp'].read_bytes(), profile=cfg['profile'], kind='lmp', reference_kind='lmp', atmosphere_policy=cfg['atmosphere_policy'])
    material_audit=None
    if cfg.get('material_policy','preserve')!='preserve':
        from .material_policy import apply_material_policy
        output,material_audit=apply_material_policy(output,cfg['resource_roots'],policy=cfg['material_policy'])
    # Check concrete new style assets. Complete material dependency closure is
    # not claimed; compiler diagnostics and user loading remain further gates.
    names = ['materials/correction/cc_c5_main.raw', 'materials/sprites/light_glow02_add_noz.vmt']
    names.extend(f'materials/skybox/sky_l4d_c5_1_hdr{face}.vmt' for face in ('bk', 'dn', 'ft', 'lf', 'rt', 'up'))
    if cfg['profile'] == 'c6-c5' and cfg['atmosphere_policy'] == 'replace':
        from .weather import SOUNDSCAPE_RESOURCES
        names.extend(SOUNDSCAPE_RESOURCES)
    if preset:
        names = preset.required_resources(cfg['atmosphere_policy'])
    notify('resources')
    assets = lookup_resources(cfg['resource_roots'], names)
    missing = [name for name, item in assets['resources'].items() if not item['found']]
    if missing:
        raise ValueError(f'Missing style resources; check resource_roots/full installation: {missing}')
    name = cfg['map_name'] if generic else MAPS[cfg['profile']]
    report = {'profile': cfg['profile'], 'source_profile': name,
              'preset': preset.metadata() if preset else None,
              'atmosphere_policy': cfg['atmosphere_policy'], 'map_name': name, 'base_style': audit,
              'mode_styles': mode_audits, 'style_resources': assets,
              'limitations': ['Resource roots are lookup candidates; game mount order must be checked in game.',
                              'C6 atmosphere follows atmosphere_policy; every new output requires independent runtime testing.',
                              'Only HDR, these two bounded profiles, and observed native resource formats are supported.']}
    if generic:
        report.update(conversion=cfg['conversion'], discovery=cfg['discovery'], limitations=[
            'Only discovered h/l/s index-zero loose entity patches are converted; no missing patches or NAV are generated.',
            'Resource roots are lookup candidates; game mount order must be checked in game.',
            'Generic atmosphere replacement remains bounded by the recorded plan; scripts and runtime visuals require independent testing.'])
    if material_audit is not None:report['material_policy']=material_audit
    return cfg, report, output, modes


def build(config_path):
    from .native import audit_bake, native, package_files
    from .model_lighting import ModelLightingEvidence
    cfg, preflight, prepared, modes = check(config_path)
    root = cfg['output_dir']
    if root.exists():
        raise ValueError('output_dir already exists. Keep its evidence; choose a fresh output_dir for a new build.')
    inventory = input_inventory(cfg)
    preset_data = None
    if cfg.get('preset'):
        from .presets import parse_preset
        preset_data = cfg['preset_file'].read_bytes()
        if parse_preset(preset_data).sha256 != preflight['preset']['sha256']:
            raise ValueError('Preset changed during preflight; use a new run')
    root.mkdir(parents=True, exist_ok=False)
    run_id = uuid.uuid4().hex[:16]
    report = {'schema_version': 1, 'run_id': run_id, 'created_at': datetime.now().astimezone().isoformat(),
              'status': 'preparing', 'profile': cfg['profile'], 'atmosphere_policy': cfg['atmosphere_policy'], 'map_name': preflight['map_name'],
              'config': cfg, 'input_inventory': inventory, 'tracked_outputs': [], 'preflight': preflight}
    if preset_data is not None:
        preset_snapshot = root / 'preset.json'
        preset_snapshot.write_bytes(preset_data)
        report['preset'] = preflight['preset'] | {'snapshot_path': str(preset_snapshot)}
        report['tracked_outputs'].append(tracked(preset_snapshot))
    manifest = root / 'run.json'
    write_json(manifest, report)
    try:
        name = report['map_name']
        compile_dir = root / 'bake'
        compile_dir.mkdir()
        vrad_game_dir = cfg['game_dir']
        if cfg.get('native_mounts', 'gameinfo') == 'resource_roots':
            from .native_mounts import write_resource_root_gameinfo
            generated_gameinfo = write_resource_root_gameinfo(compile_dir, cfg['resource_roots'])
            report['tracked_outputs'].append(tracked(generated_gameinfo))
            vrad_game_dir = compile_dir
        snapshot = compile_dir / 'input.bsp.snapshot'
        snapshot.write_bytes(prepared)
        verify_material_run(report,root)
        bsp = compile_dir / (name + '.bsp')
        bsp.write_bytes(prepared)
        print('Checking static-prop model versions and snapshotting model resources.', flush=True)
        model_evidence = ModelLightingEvidence(prepared, cfg['resource_roots'])
        report['model_resource_inventory'] = model_evidence.inventory()
        report['status'] = 'baking'
        report['vrad_command'] = ['-game', str(vrad_game_dir), '-novconfig', '-hdr', '-bounce', '4',
            '-StaticPropLighting', '-StaticPropPolys', '-TextureShadows', '-threads', str(cfg['threads']), '-low', str(bsp)]
        write_json(manifest, report)
        print(f'Baking HDR world/static-prop lighting. Log: {compile_dir / "vrad.log"}', flush=True)
        native(cfg['tools_dir'] / 'vrad.exe', report['vrad_command'], cwd=compile_dir,
               log=compile_dir / 'vrad.log', timeout=cfg['timeout_seconds'])
        verify_inventory(report['tracked_outputs'])
        compiled = bsp.read_bytes()
        model_evidence.verify_current()
        verify_material_run(report,root)
        report['bake_audit'] = audit_bake(
            prepared, compiled, model_evidence=model_evidence,
            allow_leaf_sky_flags=cfg.get('native_mounts') == 'resource_roots')
        log = (compile_dir / 'vrad.log').read_text(encoding='utf-8', errors='replace')
        report['compiler_diagnostics'] = [line for line in log.splitlines() if re.search(r'error|warning|not found|could not|couldn.t', line, re.I)]
        if any(re.search(r'Error loading studio model|Error!.*(?:material|model)|could not open.*(?:mdl|vmt)', line, re.I) for line in report['compiler_diagnostics']):
            raise ValueError('VRAD reported missing model/material inputs; inspect compiler_diagnostics and bake/vrad.log')
        from .soundscapes import preset_soundscape_assets
        preset = run_preset(report)
        payloads = {f'maps/{name}.bsp': compiled, f'maps/{name}.nav': cfg['nav'].read_bytes(),
                    'addoninfo.txt': addon_info(name, 'offline', preset_id=preset.id if preset else None)}
        payloads.update(preset_soundscape_assets(preset, name) if preset else {})
        payloads.update({f'maps/{name}_{key}_0.lmp': data for key, data in modes.items()})
        if cfg['exclude']:
            payloads[f'maps/{name}_exclude.lst'] = cfg['exclude'].read_bytes()
        report['status'] = 'packaging_offline'
        write_json(manifest, report)
        package = package_files(payloads, stage=root / 'offline', stem=f'lmc_{name}_{run_id}_offline',
                                tools_dir=cfg['tools_dir'], game_dir=cfg['game_dir'])
        report['offline_package'] = package
        report['offline_map'] = str(Path(package['vpk']).with_suffix('') / f'maps/{name}.bsp')
        report['tracked_outputs'].extend([tracked(snapshot), tracked(bsp), tracked(package['vpk'])])
        report['tracked_outputs'].extend(tracked(Path(package['vpk']).with_suffix('') / item['name']) for item in package['files'])
        verify_inventory(inventory)
        model_evidence.verify_current()
        report['status'] = 'offline_ready'
        report['reflection_strategy'] = 'original sampled/default reflection textures preserved; not recaptured'
        write_json(manifest, report)
        print(json.dumps(status_summary(report), ensure_ascii=False), flush=True)
        return report
    except Exception as exc:
        report.update(status='failed', failed_stage=report['status'], error=str(exc))
        write_json(manifest, report)
        raise


def prepare(root):
    from .reflections import prepare_capture, capture_controls
    root = Path(root).resolve()
    report = load_run(root)
    if report['status'] != 'offline_ready':
        raise ValueError('prepare-capture requires offline_ready; use status to inspect this run')
    name, alias = report['map_name'], 'mc' + report['run_id'][:8]
    baseline = Path(report['offline_map']).read_bytes()
    assets, audit = prepare_capture(baseline, map_name=name, alias=alias)
    package = report['offline_package']
    original = Path(package['vpk']).with_suffix('')
    for item in package['files']:
        if item['name'].startswith('maps/'):
            relative = 'maps/' + Path(item['name']).name.replace(name, alias, 1)
            assets[relative] = (original / item['name']).read_bytes()
    preset = run_preset(report)
    if preset:
        from .soundscapes import preset_soundscape_assets
        assets.update(preset_soundscape_assets(preset, alias))
    assets.update(capture_controls(map_name=name, alias=alias, marker='lmc_' + report['run_id'],
                                  exposure_max=preset.capture_exposure_max if preset else 5,
                                  tonemap_name=run_capture_tonemap(report)))
    capture_dir = root / 'capture'
    if capture_dir.exists():
        raise ValueError('Capture preparation directory already exists; inspect it before retrying')
    capture_dir.mkdir()
    for relative, data in assets.items():
        path = capture_dir / relative
        if not path.resolve().is_relative_to(capture_dir):
            raise ValueError('Capture output path escaped staging directory')
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open('xb') as stream:
            stream.write(data)
    report.update(status='capture_prepared', capture_alias=alias, capture_audit=audit,
                  capture_files=[{'name': name, **tracked(capture_dir / name)} for name in sorted(assets)])
    report['tracked_outputs'].extend(tracked(capture_dir / name) for name in assets)
    report['capture_instructions'] = {'game_dir': report['config']['game_dir'], 'files_to_install': str(capture_dir),
        'purpose': 'Temporary alias is for native cubemap generation only, never the delivered gameplay map.',
        'after_capture': 'Restore specular, exit game, copy the generated alias BSP and capture log to evidence/, then finish-capture.'}
    write_json(root / 'run.json', report)
    print(json.dumps({'status': report['status'], 'alias': alias, 'controls': sorted(n for n in assets if n.startswith('cfg/'))}, ensure_ascii=False))
    return report


def audit_repacked(baseline, final, replacements):
    a, b = BspFile.parse(baseline), BspFile.parse(final)
    if a.version != b.version or a.revision != b.revision or any(
            a.lumps[i] != b.lumps[i] or a.lump_bytes(i) != b.lump_bytes(i)
            for i in range(64) if i != 40):
        raise ValueError('BSPZIP changed protected data/directory metadata; final package was not created')
    if (a.lumps[40].version, a.lumps[40].fourcc) != (b.lumps[40].version, b.lumps[40].fourcc):
        raise ValueError('BSPZIP changed protected pak interpretation')
    old, new = zipfile.ZipFile(io.BytesIO(a.lump_bytes(40))), zipfile.ZipFile(io.BytesIO(b.lump_bytes(40)))
    if new.testzip() is not None or len(new.namelist()) != len(set(new.namelist())) or set(old.namelist()) != set(new.namelist()):
        raise ValueError('BSPZIP resource inventory/CRC mismatch')
    for name in old.namelist():
        if new.read(name) != replacements.get(name, old.read(name)):
            raise ValueError(f'Unexpected packed resource modification: {name}')


def finish(root, capture_path, log_path):
    try:
        return _finish(root, capture_path, log_path)
    except Exception as exc:
        manifest = Path(root).resolve() / 'run.json'
        if manifest.is_file():
            report = json.loads(manifest.read_text(encoding='utf-8'))
            report['last_capture_import_error'] = str(exc)
            write_json(manifest, report)
        raise


def _finish(root, capture_path, log_path):
    from .native import native, package_files
    from .reflections import audit_capture_log, capture_replacements
    root = Path(root).resolve()
    report = load_run(root)
    if report['status'] not in ('capture_prepared', 'capture_installed'):
        raise ValueError('finish-capture requires a prepared capture run')
    captured = Path(capture_path).read_bytes()
    log = Path(log_path).read_bytes()
    log_text = log.decode('utf-8', errors='replace')
    log_audit = audit_capture_log(log_text, alias=report['capture_alias'], marker='lmc_' + report['run_id'])
    if re.search(r"couldn't get.*(?:hdr|cubemap)|unable.*maps[/\\]" + re.escape(report['capture_alias']), log_text, re.I):
        raise ValueError('Capture log reports missing reflection resources; repair the alias installation and restart before capture')
    baseline = Path(report['offline_map']).read_bytes()
    replacements, audit = capture_replacements(baseline, captured, map_name=report['map_name'], alias=report['capture_alias'])
    audit['runtime_log'] = log_audit
    parent = root / 'reflection-import'
    parent.mkdir(exist_ok=True)
    number = 1
    while (parent / f'attempt-{number:02}').exists():
        number += 1
    stage = parent / f'attempt-{number:02}'
    stage.mkdir()
    report['latest_import_attempt'] = str(stage)
    write_json(root / 'run.json', report)
    (stage / 'captured.bsp').write_bytes(captured)
    (stage / 'capture.log').write_bytes(log)
    listing = []
    for name, data in replacements.items():
        path = stage / 'resources' / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        listing.extend([name, str(path)])
    pair_list = stage / 'replace-list.txt'
    # Native utility reads local paths. ASCII paths are supported by this
    # baseline; fail rather than silently mangle a path in a list file.
    try:
        pair_list.write_bytes(('\n'.join(listing) + '\n').encode('ascii'))
    except UnicodeEncodeError as exc:
        raise ValueError('Native BSPZIP replacement list requires an ASCII output path; choose an ASCII run directory') from exc
    output = stage / (report['map_name'] + '.bsp')
    cfg = report['config']
    native(Path(cfg['tools_dir']) / 'bspzip.exe', ['-addorupdatelist', report['offline_map'], str(pair_list), str(output), '-game', cfg['game_dir']],
           cwd=stage, log=stage / 'bspzip.log', timeout=120)
    final = output.read_bytes()
    audit_repacked(baseline, final, replacements)
    original = Path(report['offline_package']['vpk']).with_suffix('')
    payloads = {item['name']: (original / item['name']).read_bytes() for item in report['offline_package']['files']}
    payloads[f'maps/{report["map_name"]}.bsp'] = final
    preset = run_preset(report)
    payloads['addoninfo.txt'] = addon_info(report['map_name'], 'final', preset_id=preset.id if preset else None)
    package = package_files(payloads, stage=root / 'final' / f'attempt-{number:02}', stem=f'lmc_{report["map_name"]}_{report["run_id"]}_final',
                            tools_dir=Path(cfg['tools_dir']), game_dir=Path(cfg['game_dir']))
    report.update(status='final_ready_pending_user_validation', final_package=package, reflection_import=audit,
                  reflection_strategy='native HDR samples recaptured; original LDR/default reflections preserved')
    report.pop('last_capture_import_error', None)
    report['tracked_outputs'].extend([tracked(output), tracked(package['vpk']), tracked(stage / 'captured.bsp'), tracked(stage / 'capture.log')])
    verify_inventory(report['input_inventory'])
    write_json(root / 'run.json', report)
    print(json.dumps(status_summary(report), ensure_ascii=False))
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    batch = commands.add_parser('batch-check', help='Read-only batch preflight; writes reports only')
    batch.add_argument('--manifest', type=Path, required=True)
    batch.add_argument('--output', type=Path, required=True)
    batch_run_cmd = commands.add_parser('batch-run', help='Serial build and automatic capture; final packages remain uninstalled')
    batch_run_cmd.add_argument('--manifest', type=Path, required=True)
    batch_run_cmd.add_argument('--output', type=Path, required=True)
    batch_run_cmd.add_argument('--launcher', type=Path, required=True)
    analyze = commands.add_parser('analyze', help='Read-only map-independent analysis; does not build')
    analyze.add_argument('--bsp', type=Path, required=True)
    analyze.add_argument('--preset', required=True)
    analyze.add_argument('--search-dir', type=Path, action='append', default=[])
    analyze.add_argument('--resource-root', type=Path, action='append', default=[])
    for name in ('check', 'build'):
        commands.add_parser(name).add_argument('--config', type=Path, required=True)
    for name in ('prepare-capture', 'install-capture', 'collect-capture', 'remove-capture', 'finish-capture', 'status', 'auto-capture', 'recover-auto-capture'):
        cmd = commands.add_parser(name)
        cmd.add_argument('--run', type=Path, required=True)
        if name == 'auto-capture':
            cmd.add_argument('--launcher', type=Path, required=True)
        if name == 'finish-capture':
            cmd.add_argument('--bsp', type=Path, required=True)
            cmd.add_argument('--log', type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == 'batch-run':
            from .batch_run import batch_run
            report = batch_run(args.manifest, args.output, args.launcher)
            print(json.dumps({'status': report['status'], 'counts': report['counts'],
                              'output': str(args.output.resolve())}, ensure_ascii=False))
            return 0 if report['status'] == 'complete' else 2
        elif args.command == 'batch-check':
            from .batch import batch_check
            report = batch_check(args.manifest, args.output)
            print(json.dumps({'status': report['status'], 'counts': report['counts'],
                              'output': str(args.output.resolve())}, ensure_ascii=False))
            return 2 if report['counts']['failed'] else 0
        elif args.command == 'analyze':
            from .analysis import analyze_map
            report = analyze_map(args.bsp, preset_id=args.preset, search_dirs=args.search_dir,
                                 resource_roots=args.resource_root)
            print(json.dumps(report, ensure_ascii=False, indent=2))
            return 2 if report['stages']['analysis'] == 'failed' else 0
        elif args.command == 'check':
            _, report, _, _ = check(args.config)
            print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
        elif args.command == 'build':
            build(args.config)
        elif args.command == 'prepare-capture':
            prepare(args.run)
        elif args.command == 'finish-capture':
            finish(args.run, args.bsp, args.log)
        elif args.command == 'auto-capture':
            from .auto_capture import run
            run(args.run, args.launcher)
        elif args.command == 'recover-auto-capture':
            from .auto_capture import recover
            recover(args.run)
        elif args.command in ('install-capture', 'collect-capture', 'remove-capture'):
            from . import handoff
            getattr(handoff, args.command.split('-')[0])(args.run)
        else:
            print(json.dumps(status_summary(load_run(args.run)), ensure_ascii=False, indent=2))
        return 0
    except (OSError, ValueError, KeyError, subprocess.SubprocessError, zipfile.BadZipFile) as exc:
        print(f'error: {exc}', file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
