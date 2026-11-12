"""Reproducible graphify assembly for this extraction; run with saved interpreter."""
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

OUT = Path(__file__).resolve().parent
ROOT = OUT.parent
SPEC = Path('C:/Users/ABC/.agents/skills/graphify/references/extraction-spec.md')


def read(name, default=None):
    p = OUT / name
    return json.loads(p.read_text(encoding='utf-8')) if p.exists() else default


def write(name, value):
    (OUT / name).write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')


def assemble():
    from graphify.cache import save_semantic_cache
    plan = read('.graphify_chunk_plan.json')
    nodes, edges, hyperedges, failures = [], [], [], []
    for index, files in enumerate(plan, 1):
        name = f'.graphify_chunk_{index:02}.json'
        try:
            data = read(name)
            assert isinstance(data, dict) and isinstance(data['nodes'], list) and isinstance(data['edges'], list)
            # The agent API does not expose real usage; never invent counts.
            data['input_tokens'] = data['output_tokens'] = 0
            data['token_usage_status'] = 'unavailable: agent tool does not expose usage'
            write(name, data)
            nodes.extend(data['nodes'])
            edges.extend(data['edges'])
            hyperedges.extend(data.get('hyperedges', []))
        except (AssertionError, KeyError, TypeError, ValueError) as exc:
            failures.append(index)
            print(f'WARNING: chunk {index} missing/invalid: {exc}')
    if len(failures) > len(plan) / 2:
        raise SystemExit('More than half of semantic chunks failed; stopping.')
    uncached = (OUT / '.graphify_uncached.txt').read_text(encoding='utf-8').splitlines()
    saved = save_semantic_cache(nodes, edges, hyperedges, root=str(ROOT),
                                allowed_source_files=uncached, prompt_file=str(SPEC))
    cached = read('.graphify_cached.json', dict(nodes=[], edges=[], hyperedges=[]))
    semantic_nodes = list({n['id']: n for n in cached['nodes'] + nodes}.values())
    semantic = dict(nodes=semantic_nodes, edges=cached['edges'] + edges,
                    hyperedges=cached.get('hyperedges', []) + hyperedges,
                    input_tokens=0, output_tokens=0)
    write('.graphify_semantic.json', semantic)
    ast = read('.graphify_ast.json')
    merged = {n['id']: n for n in ast['nodes']}
    for n in semantic_nodes:
        merged.setdefault(n['id'], n)
    extraction = dict(nodes=list(merged.values()), edges=ast['edges'] + semantic['edges'],
                      hyperedges=semantic['hyperedges'], input_tokens=0, output_tokens=0,
                      token_usage_status='unavailable: agent tool does not expose usage')
    write('.graphify_extract.json', extraction)
    # Retain the raw relationships as an audit trail for simple-graph collapse.
    write('extraction-audit.json', extraction)
    write('extraction-coverage.json', dict(chunks=len(plan), failed_chunks=failures,
          semantic_files_dispatched=len(uncached), semantic_files_cached=saved,
          token_usage_status='unavailable: agent tool does not expose usage'))
    print(f'Merged: {len(merged)} nodes, {len(extraction["edges"])} edges; cached {saved} files')


def graph_inputs():
    from graphify.build import build_from_json
    extraction = read('.graphify_extract.json')
    return build_from_json(extraction, root=str(ROOT), directed=False), extraction


def build():
    from graphify.cluster import cluster, score_all
    from graphify.analyze import god_nodes, surprising_connections, suggest_questions
    from graphify.export import to_json
    from graphify.diagnostics import diagnose_extraction, format_diagnostic_report
    G, extraction = graph_inputs()
    if not G.number_of_nodes():
        raise SystemExit('ERROR: empty graph; existing outputs preserved.')
    communities = cluster(G)
    cohesion = score_all(G, communities)
    labels = {cid: f'Community {cid}' for cid in communities}
    analysis = dict(communities=communities, cohesion=cohesion, gods=god_nodes(G, top_n=5),
                    surprises=surprising_connections(G, communities, top_n=3),
                    questions=suggest_questions(G, communities, labels, top_n=3))
    if not to_json(G, communities, str(OUT / 'graph.json')):
        raise SystemExit('ERROR: graph shrink guard rejected export; existing output preserved.')
    write('.graphify_analysis.json', analysis)
    health = diagnose_extraction(extraction, directed=False, root=str(ROOT))
    write('graph-health.json', health)
    print(format_diagnostic_report(health))
    report(G, extraction, analysis, labels)
    print(f'Graph: {G.number_of_nodes()} nodes, {G.number_of_edges()} edges, {len(communities)} communities')
    # Supply concrete labels and sources for the human naming pass.
    for cid, members in communities.items():
        ordered = sorted(members, key=lambda n: G.degree(n), reverse=True)
        print(json.dumps(dict(community=cid, size=len(members), cohesion=cohesion[cid],
              top=[dict(id=n,label=G.nodes[n].get('label',n),source=G.nodes[n].get('source_file',''))
                   for n in ordered[:12]]), ensure_ascii=True))


def report(G, extraction, analysis, labels):
    from graphify.report import generate
    from graphify.analyze import suggest_questions
    communities = {int(k): v for k, v in analysis['communities'].items()}
    cohesion = {int(k): v for k, v in analysis['cohesion'].items()}
    questions = suggest_questions(G, communities, labels, top_n=3)
    text = generate(G, communities, cohesion, labels, analysis['gods'], analysis['surprises'],
                    read('.graphify_detect.json'), dict(input=0, output=0), str(ROOT),
                    suggested_questions=questions)
    text = text.replace('- Token cost: 0 input · 0 output',
        '- Token cost: unavailable — the agent tool does not expose actual token usage. '
        'Numeric zero fields in extraction/cost sidecars are placeholders, not measured usage; '
        'AST extraction has no LLM cost.')
    health = read('graph-health.json', {})
    flags = [(key, label) for key, label in (
        ('dangling_endpoint_edges','dangling-endpoint edges'),
        ('missing_endpoint_edges','missing-endpoint edges'),
        ('self_loop_edges','self-loop edges'),
        ('undirected_same_endpoint_collapsed_edges','collapsed undirected edges')) if health.get(key)]
    if flags:
        text += '\n\n## Graph Health Warning\n\n'
        text += '; '.join(f'{health[k]} {label}' for k, label in flags) + '.\n'
        text += ('The graph may be incomplete. These are extraction diagnostics, not application defects. '
                 'Raw nodes and relationships are retained in `extraction-audit.json`; '
                 'full diagnostic counts are in `graph-health.json`.\n')
    coverage = read('extraction-coverage.json', {})
    if coverage.get('failed_chunks'):
        text += '\nFailed semantic chunks: ' + str(coverage['failed_chunks']) + '.\n'
    (OUT / 'GRAPH_REPORT.md').write_text(text,encoding='utf-8')
    return questions


def label():
    from graphify.export import to_json
    G, extraction = graph_inputs()
    analysis = read('.graphify_analysis.json')
    labels = {int(k):v for k,v in read('.graphify_labels.json').items()}
    communities = {int(k):v for k,v in analysis['communities'].items()}
    if not to_json(G, communities, str(OUT/'graph.json'), community_labels=labels):
        raise SystemExit('ERROR: graph shrink guard rejected label export.')
    report(G, extraction, analysis, labels)
    print('Community labels and report updated')


def finish():
    from graphify.detect import save_manifest
    from graphify.cli import _stamped_manifest_files
    detect = read('.graphify_detect.json'); extraction = read('.graphify_extract.json')
    corpus = detect.get('all_files') or detect['files']
    files = _stamped_manifest_files(corpus, extraction, ROOT)
    dispatched = {f for t, fs in detect['files'].items() if t in ('document','paper','image') for f in fs}
    stamped = {f for fs in files.values() for f in fs}
    scan = {f for fs in corpus.values() for f in fs}
    save_manifest(files,root=str(ROOT),scan_corpus=scan,clear_semantic=(dispatched-stamped) or None)
    cost = read('cost.json', dict(runs=[],total_input_tokens=0,total_output_tokens=0))
    cost['runs'].append(dict(date=datetime.now(timezone.utc).isoformat(), input_tokens=0,
        output_tokens=0, files=detect['total_files'], token_usage_status='unavailable'))
    cost['token_usage_note'] = 'Zero counters for this run are placeholders: agent tool usage is unavailable.'
    write('cost.json',cost)
    for name in ('.graphify_detect.json','.graphify_extract.json','.graphify_ast.json',
                 '.graphify_semantic.json','.graphify_analysis.json','.graphify_cached.json',
                 '.graphify_uncached.txt','.graphify_semantic_new.json','.needs_update'):
        (OUT/name).unlink(missing_ok=True)
    for pattern in ('.graphify_chunk_*.json','.graphify_prompt_*.txt'):
        for p in OUT.glob(pattern):
            p.unlink()
    print('Manifest and cost tracker saved. Token usage unavailable; no invented counts.')


if __name__ == '__main__':
    {'assemble':assemble,'build':build,'label':label,'finish':finish}[sys.argv[1]]()
