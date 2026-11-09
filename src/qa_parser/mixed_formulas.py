"""Apply field-aware formula policy before materializing customer text/resources."""
import re
from .config import digest
from .formula_policy import decide, apply_field_context

REF = re.compile(r'⟦(F\d{6})⟧')


def apply_mixed_formulas(questions, formulas, assets, blocks, root, config):
    from .core import safe_file, _save_bytes
    from .formula_images import preview_to_png
    from .formula_raster import render_formulas
    fmap = {f['id']: f for f in formulas}
    policies = {f['id']: decide(f['latex']) for f in formulas
                if f['status'] == 'converted' and f.get('latex')}
    selected = set()
    for q in questions:
        fields = [q['stem'], q['answer'], q['analysis'], *q['options'].values()]
        if '选择项' in config.get('choice_columns', []):
            fields.append('\n'.join(q['options'].values()))
        for value in fields:
            ids = [fid for fid in REF.findall(value) if fid in policies]
            selected.update(ids)
            apply_field_context(ids, fmap, policies)
    pending = []
    def attach(f, result, origin):
        iid = f'I{len(assets)+1:06d}'
        name = f'{len(assets)+1:03d}.png'
        resource = _save_bytes(root, f'assets/{name}', result['png'])
        asset = {'id': iid, 'filename': name, 'extension': 'png', 'location': f['location'],
                 'origin': origin, 'formula_id': f['id'], 'engine': result['engine'],
                 'width': result['width'], 'height': result['height'], 'review_reason': '', **resource}
        if origin == 'formula_preview':
            asset['source_preview_sha256'] = f['preview']['sha256']
        else:
            asset.update(source_formula_sha256=f['source']['sha256'], latex_sha256=digest(f['latex'].encode()),
                         renderer_resources_sha256=result['renderer_resources_sha256'])
            f['raster_evidence'] = {k:v for k,v in result.items() if k != 'png'}
        assets.append(asset)
        f.update(status='image', fallback_asset_id=iid, fallback_engine=result['engine'])
    for f in formulas:
        fid = f['id']
        if fid not in selected:
            continue
        policy = policies[fid]
        f['output_policy'] = policy
        if policy['mode'] == 'text':
            continue
        f['conversion_status'] = f['status']
        f['status'] = 'review'  # fail closed until a reliable image is attached
        f['reason'] = '混合输出需要公式图片：' + policy['reason']
        preview = f.get('preview')
        if preview and (f.get('preview_binding') or {}).get('verified'):
            data = safe_file(root, preview['path']).read_bytes()
            if digest(data) != preview['sha256']:
                f['fallback_error'] = 'Original preview changed'
                continue
            original = preview_to_png(data, safe_file(root, preview['path']).suffix)
            if original['status'] == 'converted':
                attach(f, original, 'formula_preview')
                continue
            f['original_preview_error'] = original['reason']
        if policy['mode'] == 'image':
            pending.append(f)
        else:
            f['fallback_error'] = 'Markup or delimiter pollution cannot be trusted for rerendering'
    results = render_formulas([f['latex'] for f in pending])
    for f in pending:
        result = results.get(f['latex'], {'status':'failed', 'reason':'Missing native raster result'})
        if result['status'] == 'converted':
            attach(f, result, 'formula_rerender')
        else:
            f['fallback_error'] = result['reason']
    # Number ALL images by original document occurrence, including newly
    # generated formulas, without overwriting any internal evidence file.
    amap = {a['id']:a for a in assets}
    seen = set()
    for block in blocks:
        for token in block['tokens']:
            aid = token.get('id') if token['kind'] == 'image' else None
            if token['kind'] == 'formula':
                aid = fmap[token['id']].get('fallback_asset_id')
            if aid in amap and aid not in seen:
                seen.add(aid)
                amap[aid]['filename'] = f'{len(seen):03d}.' + amap[aid]['extension']
