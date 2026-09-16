"""V2 weather evidence: resource identities and event edges, never map names.

Controllers stay in place. Context travels only through exclusively weather-
activated relay OnTrigger events. A different event on the same entity is not
evidence, nor is reachability alone permission to delete an upstream entity.
"""
from collections import defaultdict
import re

from .atmosphere_analysis import entity_value


EXTRA_INPUTS = {
    'env_fog_controller': {'setcolorlerpto', 'setcolorsecondarylerpto', 'setstartdistlerpto',
                           'setenddistlerpto', 'setmaxdensitylerpto', 'set2dskyboxfogfactorlerpto'},
    'func_precipitation': {'alpha'},
    'ambient_generic': {'playsound', 'stopsound', 'volume', 'pitch', 'fadein', 'fadeout'},
    'sound_mix_layer': {'level'},
}
# Exact engine resource identities. Ambiguous helicopter/wind assets additionally
# require every activation/write to belong to a proven weather event.
THUNDER_SOUNDS = {'weather.thunder_close_all_4'}
CONTEXT_SOUNDS = {'hospital.helicopterwindloop', 'ambient/wind/windgust_strong.wav'}
CONTEXT_MIXERS = {'stormlayer', 'voiplayer'}
WIND_CALM = {'minwind': '0', 'maxwind': '0', 'mingust': '0', 'maxgust': '0'}


def weather_evidence(entities, graph):
    classes = [entity_value(e, 'classname') for e in entities]
    outgoing, incoming = defaultdict(list), defaultdict(list)
    for o in graph['outputs']:
        outgoing[o['entity_index']].append(o)
        for target in o['candidate_targets']:
            incoming[target].append(o)

    def event(o):
        return o['entity_index'], o['output'].lower()

    def scripted(i):
        return entity_value(entities[i], 'vscripts') or entity_value(entities[i], 'thinkfunction')

    thunder = {i for i, e in enumerate(entities) if classes[i] == 'ambient_generic'
               and (entity_value(e, 'message') or '').lower() in THUNDER_SOUNDS}
    rain = {i for i, cls in enumerate(classes) if cls == 'func_precipitation'}
    events = {event(o) for o in graph['outputs'] if not scripted(o['entity_index'])
              and any(j in rain | thunder for j in o['candidate_targets'])}
    reasons = {key: 'direct_precipitation_or_thunder' for key in events}
    # Relay disable/kill affect scheduling, not independent activation. Unknown
    # inputs, scripts, FireUser and AddOutput prevent inferred relay context.
    while True:
        added = set()
        for i, cls in enumerate(classes):
            if cls != 'logic_relay' or scripted(i):
                continue
            ins = incoming[i]
            if any(o['input'].lower() not in {'trigger', 'enable', 'disable', 'toggle',
                                             'kill', 'cancelpending'} for o in ins):
                continue
            activations = [o for o in ins if o['input'].lower() == 'trigger']
            if activations and all(event(o) in events for o in activations):
                added.add((i, 'ontrigger'))
        added -= events
        if not added:
            break
        events.update(added)
        reasons.update({key: 'exclusive_relay_trigger_callers' for key in added})

    removed = set(thunder)
    ownership = {i: 'catalogued_thunder_sound' for i in thunder}
    for i, e in enumerate(entities):
        cls = classes[i]
        contextual = ((cls == 'ambient_generic' and
                       (entity_value(e, 'message') or '').lower() in CONTEXT_SOUNDS)
                      or (cls == 'sound_mix_layer' and
                          (entity_value(e, 'MixLayerName') or '').lower() in CONTEXT_MIXERS))
        writes = [o for o in incoming[i] if o['input'].lower() != 'kill']
        if contextual and writes and all(event(o) in events for o in writes):
            removed.add(i)
            ownership[i] = 'catalogued_asset_with_exclusive_weather_writers'

    cuts = {}
    for o in graph['outputs']:
        targets = o['candidate_targets']
        if not targets:
            continue
        # A scalar assignment only. Never interpret or execute command strings.
        command = o['parameter'].strip()
        if (o['input'].lower() == 'command' and
                re.fullmatch(r'r_skyboxfogfactor\s+[+-]?(?:\d+(?:\.\d*)?|\.\d+)', command, re.I)
                and 0 <= float(command.split()[1]) <= 1
                and all(classes[j] == 'point_broadcastclientcommand' for j in targets)):
            cuts[o['entity_index'], o['pair_index']] = 'exact_skybox_fog_assignment'
        # Camera-only shake: retain the endpoint and all unrelated caller edges.
        if (event(o) in events and o['input'].lower() in {'startshake', 'stopshake', 'amplitude', 'frequency'}
                and all(classes[j] == 'env_shake' and
                        int(entity_value(entities[j], 'spawnflags') or '0') & (8 | 16) == 0 for j in targets)):
            if any(x['input'].lower() not in {'startshake', 'stopshake', 'amplitude', 'frequency', 'kill'}
                   for j in targets for x in incoming[j]):
                raise ValueError('Camera shake runtime mutation requires review')
            exclusive = all(event(x) in events for j in targets for x in incoming[j]
                            if x['input'].lower() != 'kill')
            if o['input'].lower() == 'startshake' or exclusive:
                cuts[o['entity_index'], o['pair_index']] = 'weather_event_camera_shake'
    for i in removed:
        if scripted(i):
            raise ValueError(f'Weather endpoint script requires review: {i}')
    for o in graph['outputs']:
        if (o['entity_index'], o['pair_index']) in cuts:
            if any(scripted(j) or outgoing[j] for j in o['candidate_targets']):
                raise ValueError('Weather edge target has script or outputs requiring review')
    return dict(removed=removed, cuts=cuts, audit={
        'resource_catalog': 'weather-assets-v1',
        'owned_endpoints': [dict(entity_index=i, reason=ownership[i]) for i in sorted(removed)],
        'weather_events': [dict(entity_index=i, output=key, reason=reasons[i, key]) for i, key in sorted(events)],
        'unresolved_weather_outputs': [dict(entity_index=o['entity_index'], output=o['output'],
                                            target=o['target'], input=o['input'])
                                       for o in graph['outputs'] if event(o) in events and not o['candidate_targets']],
        'limitations': ['Only catalogued weather assets are classified; unknown particles/audio/scripts remain.',
                       'Mixed or externally activated relay events are not inferred as weather-only.',
                       'Wind template entities and their spawn/kill lifecycle are preserved; wind values are normalized.'],
    })
