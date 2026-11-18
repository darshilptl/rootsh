"""Shortcut audit for researchwork/data/dataset/records.jsonl.
Run from a COPY of researchwork:  python3 dataset_shortcuts.py <path/to/records.jsonl>"""
import collections, json, re, sys
rs = [json.loads(l) for l in open(sys.argv[1])]
strip = lambda a: re.sub(r'^Variant \d+:\s*', '', a)
tr = [r for r in rs if r['split'] == 'train']; ho = [r for r in rs if r['split'] == 'heldout']
def lookup_acc(key):
    m = collections.defaultdict(collections.Counter)
    for r in tr: m[key(r)][r['label']] += 1
    return sum((m[key(r)].most_common(1)[0][0] if m[key(r)] else 'risky') == r['label'] for r in ho) / len(ho)
print('rows', len(rs), '| unique user_request', len({r['user_request'] for r in rs}))
print('actions starting "Variant N:"', sum(r['proposed_action'].startswith('Variant') for r in rs))
print('(source,label) counts', collections.Counter((r['source'], r['label']) for r in rs))
print('reasoning auto-generated', sum(r['reasoning'].startswith('Auto-generated') for r in rs), '| reviewed=true', sum(r['reviewed'] for r in rs))
fs = {r['family_id']: r['split'] for r in rs}; tw = {r['family_id']: r['twin_of'] for r in rs}
print('families whose twin is in the other split', sum(1 for f, t in tw.items() if t and t in fs and fs[f] != fs[t]))
print('heldout acc, filename contains _a_ :', round(lookup_acc(lambda r: 'target_a' in r['proposed_action'] or 'content_a' in r['proposed_action']), 4))
print('heldout acc, (source, first word, _a_) lookup:', round(lookup_acc(lambda r: (r['source'] == 'user_direct', strip(r['proposed_action']).split()[0], 'target_a' in r['proposed_action'] or 'content_a' in r['proposed_action'])), 4))
