"""Inspect coverage or replay ONE saved AI request with an eight-second limit.

No full-roster instantiation, matches or server startup. Source-text feature
tags guide future sampling; they do not claim runtime coverage of every skill.
"""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[1]
REPORT=ROOT/'reports/ai-general-evaluation-20261120'
OLD_CASES=['equipped-electronic-24','chess-electronic-24','electronic-24','chess-hell-24','standard-24',
           'reaction-electronic-24','swap-electronic-24','swap-hell-24','teleport-electronic-24']
KEYWORDS={'summons':('召唤',),'area':('周围','范围','×','*'),'mobility':('位移','瞬移','交换','移动'),
          'random':('随机','硬币','骰'),'defense':('护盾','魔免','物免'),
          'resources':('魔力点','回魔','回复'),'death':('复活','破坏时','死'),
          'perception':('隐身','分身'),'control':('操作者','所属','控制'),'reactions':('反击','反伤','连锁')}


def cases():
    new=sorted(p.name.removesuffix('-fixture.json') for p in (REPORT/'fixtures').glob('*-fixture.json')
               if p.name!='autochess-history-24-fixture.json')
    return OLD_CASES+new


def coverage(rotation):
    sys.path.insert(0,str(ROOT/'src'))
    from wujiang.tactical.heroes.excel_roster_data import EXCEL_HERO_SPECS
    from wujiang.tactical.heroes.registry import IMPLEMENTED_EXCEL_HERO_CODES,HERO_REGISTRY
    specs={s['code']:s for s in EXCEL_HERO_SPECS if s['code'] in IMPLEMENTED_EXCEL_HERO_CODES}
    present,actors=set(),set()
    fixtures=[]
    for name in cases()[len(OLD_CASES):]:
        data=json.loads((REPORT/'fixtures'/(name+'-fixture.json')).read_text(encoding='utf-8'))
        present.update(h['code'] for h in data['heroes']);actors.add(data['actor'])
        fixtures.append({'case':name,'units':data['actual_units'],'actor':data['actor'],
                         'clones':data.get('clones',0),'dead_history':data.get('dead_history',0),
                         'real_equipment_stats_and_synergies':data.get('real_equipment_stats_and_synergies',False)})
    inventory=[]
    for code,s in sorted(specs.items()):
        text=s['raw_skill_text']+' '+s['raw_trait_text']
        inventory.append({'code':code,'name':s['name'],'role':s['role'],'race':s['race'],
            'features':[tag for tag,words in KEYWORDS.items() if any(w in text for w in words)],
            'in_mixed_fixture':code in present,'evaluated_actor':code in actors})
    pending=[r for r in inventory if not r['evaluated_actor']]
    if pending:
        start=(rotation*12)%len(pending)
        pending=pending[start:]+pending[:start]
    return {'public_excel_count':len(specs),'public_total_count':len(specs)+sum(
                not c.startswith(('excel_','strategy_')) for c in HERO_REGISTRY),
            'mixed_present_heroes':len(present),'mixed_actor_heroes':len(actors),
            'fixed_old_cases':len(OLD_CASES),'mixed_cases':len(fixtures),'fixtures':fixtures,
            'next_actor_rotation':pending[:12],'inventory':inventory,
            'coverage_limit':'presence and source-text tags are not executed-skill or full-match coverage'}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--list',action='store_true')
    parser.add_argument('--coverage',action='store_true')
    parser.add_argument('--rotation',type=int,default=0)
    parser.add_argument('--case',choices=cases())
    parser.add_argument('--source',choices=('current','before','verified'),default='current')
    args=parser.parse_args()
    if args.coverage:
        data=coverage(args.rotation)
        (REPORT/'coverage.json').write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps({k:v for k,v in data.items() if k not in ('inventory','next_actor_rotation','fixtures')},ensure_ascii=True))
    elif args.case:
        env=os.environ.copy()
        replay_report=REPORT
        label='before-final' if args.source=='before' else 'verified' if args.source=='verified' else 'manual'
        if args.source=='verified':
            latest=ROOT/'reports/ai-position-purity-20261120'
            if (latest/'validated-release').is_dir():
                replay_report=latest
                env['AI_BENCH_SOURCE']=str(latest/'validated-release')
            else:
                env['AI_BENCH_SOURCE']=str(next(path for path in (REPORT/'verified-head',REPORT/'verified-final',REPORT/'verified') if path.is_dir()))
        subprocess.run([sys.executable,str(replay_report/'bench.py'),args.case,label],env=env,check=True)
        status=json.loads((replay_report/(args.case+'-'+label+'.status.json')).read_text(encoding='utf-8'))
        if status['exit'] != 0:
            raise SystemExit('Bounded AI request failed: '+str(status['exit'])+'; see its saved log.')
    else:
        print('\n'.join(cases()))


if __name__=='__main__':
    main()
