"""Map-independent read-only preflight; no conversion eligibility promise."""
from pathlib import Path

from . import __version__
from .atmosphere_analysis import analyze_entities
from .configuration import file_hash
from .discovery import discover_inputs
from .inspect import container, entity_list, inspect_bytes, sha256
from .presets import load_preset
from .resources import lookup_resources


def analyze_map(source_bsp, *, preset_id, search_dirs=(), resource_roots=()):
    preset = load_preset(preset_id)
    inputs = discover_inputs(source_bsp, search_dirs=search_dirs)
    roots = []
    for raw in resource_roots:
        root = Path(raw).resolve(strict=True)
        if not root.is_dir():
            raise ValueError(f'Resource root is not a directory: {root}')
        roots.append(root)
    report = dict(schema_version=1, analysis_version=1, tool_version=__version__,
                  inputs=inputs, preset=preset.metadata(), containers={}, findings=[],
                  stages=dict(analysis='running', conversion='not_run', bake='not_run',
                              capture='not_run', packaging='not_run', runtime_acceptance='not_run'),
                  limitations=[
                      'Analysis completion is not conversion support or runtime acceptance.',
                      'No modification plan is executable in this stage; old build/check restrictions remain.',
                      'Role counts and IO candidates are observations, not removal permission.',
                      'Source resources are inventoried, not transitively validated; runtime mounts are unverified.',
                      'Reflection and model-format inventories do not replace bake/capture audits.'])

    def finding(code, severity='warning', **details):
        report['findings'].append(dict(code=code, severity=severity, **details))

    selected = [('base', inputs['source_bsp'], 'bsp')]
    selected.extend((key, value['selected'], 'lmp') for key, value in inputs['mode_lmps'].items())
    for key, item, kind in selected:
        result = report['containers'][key] = dict(input=item, kind=kind)
        data = Path(item['path']).read_bytes()
        if sha256(data) != item['sha256']:
            raise ValueError(f'Input changed during analysis: {item["path"]}')
        try:
            result['inspection'] = inspect_bytes(data, kind=kind)
        except ValueError as exc:
            finding('container_error', 'error', container=key, message=str(exc))
            continue
        errors = {k: v for k, v in result['inspection'].items() if k.endswith('_error')}
        for code, message in errors.items():
            finding(code, 'error', container=key, message=message)
        if 'entity_error' not in errors:
            try:
                result['atmosphere'] = analyze_entities(entity_list(container(data, kind)))
            except ValueError as exc:
                finding('entity_analysis_error', 'error', container=key, message=str(exc))
                continue
            for issue in result['atmosphere']['issues']:
                finding(issue['code'], 'error', container=key,
                        **{k: v for k, v in issue.items() if k != 'code'})
            if result['atmosphere']['script_entrypoints']:
                finding('script_semantics_unreviewed', container=key,
                        count=len(result['atmosphere']['script_entrypoints']))
            if any(o['resolution'] in ('dynamic_context', 'unresolved') for o in result['atmosphere']['outputs']):
                finding('runtime_targets_unresolved', container=key)
        if kind == 'bsp':
            lump42 = result['inspection']['lumps'][42]
            if not lump42['length']:
                finding('no_cubemap_samples', container=key)
            elif lump42['fourcc'] != '00000000' or lump42['length'] % 16:
                finding('unsupported_cubemap_records', container=key)
            for lump in result['inspection']['lumps']:
                if lump['length'] and lump['fourcc'] != '00000000':
                    finding('compressed_lump_requires_capability_check', container=key, lump=lump['index'])
        else:
            base_revision = report['containers']['base'].get('inspection', {}).get('revision')
            if base_revision is not None and result['inspection']['revision'] != base_revision:
                finding('mode_revision_differs', container=key, base_revision=base_revision,
                        mode_revision=result['inspection']['revision'])
    if inputs['nav']['status'] != 'found':
        finding('nav_not_found_in_search_dirs')
    if inputs['unrecognized_companions']:
        finding('unrecognized_companion_names', paths=[x['path'] for x in inputs['unrecognized_companions']])
    for key, value in inputs['mode_lmps'].items():
        if key.split('_')[1] != '0':
            finding('nonzero_patch_index_requires_semantics_review', container=key)
        if key.split('_')[0] not in 'hls':
            finding('unmapped_mode_token', container=key)
    if roots:
        resources = lookup_resources(roots, preset.required_resources('replace'))
        missing = sorted(name for name, entry in resources['resources'].items() if not entry['found'])
        report['resource_check'] = dict(status='missing_candidates' if missing else 'candidates_found',
                                       missing=missing, lookup=resources,
                                       scope='Preset resource candidates in explicit roots only; BSP PAK entries '
                                             'are separately inventoried and not resolved as runtime overrides.')
        if missing:
            finding('preset_resources_not_found_in_roots', names=missing)
        if resources['errors']:
            finding('resource_packages_unreadable', packages=resources['errors'])
    else:
        report['resource_check'] = dict(status='not_checked', reason='No resource roots provided')
    # Check selected and shadowed companions as well as the explicit source.
    tracked = [inputs['source_bsp'], *inputs['unrecognized_companions']]
    for choice in [inputs['nav'], inputs['exclude'], *inputs['mode_lmps'].values()]:
        tracked.extend(choice['candidates'])
    for item in tracked:
        if file_hash(item['path']) != item['sha256']:
            raise ValueError(f'Input changed during analysis: {item["path"]}')
    report['stages']['analysis'] = ('failed' if any(x['severity'] == 'error' for x in report['findings'])
                                    else 'completed_with_findings' if report['findings'] else 'completed')
    return report
