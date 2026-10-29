"""Explicit schema inspection shared by application startup and upgrade tooling."""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def specifications():
    folder = ROOT / '数据库设计'
    return (json.loads((folder / 'schemas.json').read_text('utf-8')),
            json.loads((folder / '第二阶段/运行规范/schemas.json').read_text('utf-8')))


def same_options(info, spec):
    o = info.get('options', {})
    return (o.get('validator') == spec['validator'] and o.get('validationLevel', 'strict') == 'strict'
            and o.get('validationAction', 'error') == 'error')


def check_indexes(database, name, spec):
    actual = {x['name']: x for x in database[name].list_indexes()}
    if set(actual) - {'_id_', *(x['name'] for x in spec['indexes'])}:
        raise RuntimeError(f'集合{name}含未知索引；需显式审查。')
    for x in spec['indexes']:
        found = actual.get(x['name'])
        if not found or list(found['key'].items()) != list(x['key'].items()) or any(
                found.get(k, False if k in ('unique', 'sparse') else None) != val for k, val in x.items() if k not in ('key', 'name')):
            raise RuntimeError(f'集合{name}索引{x["name"]}缺失或漂移；需显式审查，不能覆盖。')


def inspect_stage(database):
    base, target = specifications()
    infos = {x['name']: x for x in database.list_collections()}
    unknown = {n for n in infos if n not in target['collections'] and not n.startswith('system.')}
    if unknown:
        raise RuntimeError('数据库含未知集合；请显式审查，不自动覆盖。')
    names = set(infos) & set(target['collections'])
    if not names:
        # A genuinely empty application database only; do not adopt unrelated data.
        if any(not x.startswith('system.') for x in infos):
            raise RuntimeError('数据库含未知集合，不能作为空应用库初始化。')
        return 0
    for stage, spec in ((2, target), (1, base)):
        if names == set(spec['collections']) and all(same_options(infos[n], cfg) for n, cfg in spec['collections'].items()):
            for n, cfg in spec['collections'].items():
                check_indexes(database, n, cfg)
            if database.responses.find_one({'schemaVersion': {'$ne': 2}}, {'_id': 1}):
                raise RuntimeError('存在responses旧格式；保留数据，需另行审查序号迁移。')
            if stage == 2:
                run = database.migration_runs.find_one({'planId': 'P2_ADD_REFERENCES_1'})
                if run and run['state'] not in ('ACTIVATED', 'ROLLED_BACK'):
                    raise RuntimeError('升级尚未激活；请保持停写并完成verify/activate，不得混跑。')
            return stage
    raise RuntimeError('数据库结构未知或处于升级中间态；请运行phase2_schema预检/续跑，不会覆盖或删除历史数据。')


def install_empty(database, spec=None):
    spec = spec or specifications()[1]
    for name, cfg in spec['collections'].items():
        database.create_collection(name, validator=cfg['validator'], validationLevel='strict', validationAction='error')
        for index in cfg['indexes']:
            database[name].create_index(list(index['key'].items()), **{k: v for k, v in index.items() if k != 'key'})
