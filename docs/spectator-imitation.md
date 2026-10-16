# 观战模仿

用观战录像学「下一步大概是移动、攻击、技能，还是结束回合」。它不在合法动作清单里给每一格打分。录像没有记下当时有哪些合法走法，只留下走完之后的局面，所以现在只能学这四类。

设置里的「模型」会读 `var/nn-lab/weights.npz`。网络先选一类，再在这一步已经列出来的合法行动里收成一步。没有这个文件时，模型档会尽量普攻。权重和整理后的样本都在 `var/`，不进仓库。新的录像放在 `replayData/replays/`，这个目录同样不要提交。

在 `wujiang_game` 目录下执行，需要 `PYTHONPATH=src`。

```bash
python -m wujiang.tactical.rooms.imitation.train inspect --replays replayData/replays
python -m wujiang.tactical.rooms.imitation.train train --replays replayData/replays --out var/nn-lab/weights.npz
```

单个文件默认不超过 20MB。留出准确率写在同一次训练打印的结果里。低于「永远猜最多的那一类」时，不要把它当成完整评分的替代。

## 这 14 个数是什么

按顺序：血量比例、魔力比例、攻击、防御、速度、攻击范围、存活敌人数、存活友军数、最近敌人距离、是否人数落后、是否隐身、战斗轮次、是否已经在攻击范围内、最弱敌人的血量比例。

标签由相邻两步观战局面对比得到：人走了是 `move`，敌方掉血是 `attack`，自己扣了魔是 `skill`，其余能对上的行动记为 `end`。

## 决策要细到哪一步

对局里的 AI 一次只决定一步，不会同时输出「先冲上去，再放穿刺」。穿刺如果自己带冲刺，那一步的标签就是 `skill:pierce` 加上它打中的格子。普通移动和穿刺是前后两步，各记一条。

所以网络要学的不是整张棋盘上随便点一格，也不是只有「移动 / 攻击 / 技能 / 结束」四个字。对齐现有 AI 的粒度是：

- 一步合法行动：`move`、`attack`，或 `skill:穿刺的代码`
- 再加上这一步的目标单位和格子
- 上一步座位视角里已经列出来的其他合法行动，用来知道它没选什么

完整录像里，单位自己带着技能卡（代码、耗魔、冷却、目标方式），座位视角里常常还有当时的合法清单，新出现的画面事件里则有实际打出的技能代码和目标格。没有画面事件时，用技能卡的使用次数、位移和敌方掉血来补标签。

```bash
python -m wujiang.tactical.rooms.imitation.decisions --replays replayData/replays --out var/nn-lab/decisions.jsonl
```

样本和 `decision-report.json` 写在 `var/nn-lab/`，不进仓库。可见局面版写到 `var/nn-lab/visible-decisions.jsonl`：每个行动带上该武将的技能、特性、状态，以及他当时能看见的队友和敌人。对方隐身不在里面。

```bash
python -m wujiang.tactical.rooms.imitation.decisions --replays replayData/replays --out var/nn-lab/visible-decisions.jsonl
python -m wujiang.tactical.rooms.imitation.ranker --records var/nn-lab/visible-decisions.jsonl --out var/nn-lab/ranker.npz
```

排序网络按武将分开。大块样本仍在 `var/nn-lab/heroes/`，不进仓库。留出结果更好、可以上场的权重在 `data/hero-models/`。没有这份权重的武将用完整 AI。步兵、弓兵、守备兵不使用网络。

```bash
python -m wujiang.tactical.rooms.imitation.hero_models split --records var/nn-lab/visible-decisions.jsonl
python -m wujiang.tactical.rooms.imitation.hero_models train
```
