# 观战模仿

用观战录像学「下一步大概是移动、攻击、技能，还是结束回合」。它现在不接进实战，也还不会在一份合法动作清单里挑具体那一格。等录像更多了，用下面的方法重新训练即可。

录像放在项目根目录的 `replays/`。一份文件是一局结束时写下的 JSON，里面要有 `steps[].spectator_battle`。没有逐步局面的文件会被跳过。单个文件默认不超过 20MB，更大的用 `--max-bytes` 放开。

在 `wujiang_game` 目录下执行，需要 `PYTHONPATH=src`。

## 查看有多少样本

```bash
python -m wujiang.tactical.rooms.imitation.train inspect
```

## 训练

```bash
python -m wujiang.tactical.rooms.imitation.train train
```

权重写到 `var/imitation/weights.npz`。同一目录的 `report.json` 里有训练准确率、留出对局的准确率，以及「永远猜最多的那一类」能对上多少。留出按整份录像划分，不把同一局的前后步拆进两边。

## 使用已经训好的模型

命令行传入 14 个局面数字：

```bash
python -m wujiang.tactical.rooms.imitation.train predict --features 0.8,0.5,0.3,0.2,0.4,0.1,0.3,0.3,0.2,0,0,0.1,1,0.4
```

代码里：

```python
from wujiang.tactical.rooms.imitation import collect, load_picker, predict, summarize, train

summarize(collect())
report = train()
print(predict([0.8, 0.5, 0.3, 0.2, 0.4, 0.1, 0.3, 0.3, 0.2, 0, 0, 0.1, 1, 0.4], load_picker()))
```

`predict` 返回 `label`（`move`、`attack`、`skill`、`end` 之一）和四类概率。

## 这 14 个数是什么

按顺序：血量比例、魔力比例、攻击、防御、速度、攻击范围、存活敌人数、存活友军数、最近敌人距离、是否人数落后、是否隐身、战斗轮次、是否已经在攻击范围内、最弱敌人的血量比例。距离和人数在写入前已经缩放到 0 到 1 附近。

标签不是录像里的原始字段。上一步观战局面里的行动武将，和这一步局面对比：人走了是 `move`，敌方掉血是 `attack`，自己扣了魔是 `skill`，其余能对上的行动记为 `end`。

## 以后换成合法动作

等录像里带上那一步的合法动作列表，再改 `dataset.py` 的标签和 `model.py` 的输出。调用方式保持 `collect`、`summarize`、`train`、`load_picker`、`predict` 不变。
