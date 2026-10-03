#!/usr/bin/env python3
"""PyRPG_Mini 缺陷复现脚本（按 Game 的回合流程驱动）。

复现 5 类问题:
  1. 装备/结算导致属性重复加成: 胜利结算的难度曲线被重复应用、
     装备破损状态丢失、缺少装备装配/脱卸后从当前装备重算属性的机制
  2. 敌人逃跑/死亡后仍掉落奖励、死亡敌人仍行动
  3. 护甲耐久或价格越界: 耐久变为负数、修理费超过耐久上限、
     铁匠铺金币被扣成负数
  4. 数据库重建产生重复行、初始化失败后旧表残留
  5. 输入非法数值导致计算异常: loadgame 崩溃、item_management 负索引

输出 [BUG] 表示问题仍存在, [OK] 表示已修复。退出码 = 仍存在的问题数。

运行: python3 reproduce_bugs.py
"""

import contextlib
import io
import os
import shutil
import sys
import tempfile
from unittest import mock

import Armor
import Enemy
import Game
import Hero
import Item
import Shield
import Weapon
import dbsetup

RESULTS = []


def report(name, ok, detail=''):
    RESULTS.append((name, ok))
    print(('[OK]  ' if ok else '[BUG] ') + name + ((' -- ' + detail) if detail else ''))


def quiet():
    return contextlib.redirect_stdout(io.StringIO())


def make_hero():
    hero = Hero.Hero('warrior', 1, 20, 7, 3, 10 ** 9, 0)
    hero.name = 'Tester'
    hero.defcurve = 0.2
    hero.atkcurve = 0.2
    # 固定初始装备数值, 避免品质随机化干扰断言
    hero.ourweapon.baseatk = hero.ourweapon.atk = 3
    hero.ourweapon.maxdur = hero.ourweapon.dur = 20
    hero.ourarmor.basedefn = hero.ourarmor.defn = 2
    hero.ourarmor.maxdur = hero.ourarmor.dur = 10
    hero.ourshield.basedefn = hero.ourshield.defn = 3
    hero.ourshield.maxdur = hero.ourshield.dur = 20
    hero.applyequip()
    return hero


def make_game(hero, enemy):
    game = Game.Game.__new__(Game.Game)
    game.suspensemode = 0
    game.debugging = 0
    game.riddlemode = 0
    game.autoattack = 0
    game.ourhero = hero
    game.ourenemy = enemy
    game.textwidth = 70
    game.datawidth = 55
    return game


# --- Bug 1a: 胜利结算时难度曲线被重复加成 -------------------------------------
def check_victory_loot_duplication():
    hero = make_hero()
    enemy = Enemy.Enemy(1, 'Timid', 'Corny', 'Duck', 5, 10, 100, 1, 0, 'none')
    game = make_game(hero, enemy)
    hero.isbattling = True
    with mock.patch('builtins.input', return_value=''), quiet():
        game.battle()  # playerturn(攻击) -> enemy 死亡 -> victory 结算
    expected_gold = int(100 + 100 * hero.defcurve)
    expected_xp = int(10 + 10 * hero.defcurve)
    ok = hero.gold == expected_gold and hero.xp == expected_xp
    report('1a 胜利结算金币/经验的难度曲线只应用一次',
           ok, 'got gold=%d xp=%d, expected gold=%d xp=%d'
               % (hero.gold, hero.xp, expected_gold, expected_xp))


# --- Bug 1b: 装备装配/替换/脱卸必须从当前装备重算 -----------------------------
def check_equipment_recompute():
    hero = make_hero()
    baseatk = hero.baseatk
    if not hasattr(hero, 'equip') or not hasattr(hero, 'unequip'):
        report('1b 装备装配/替换/脱卸后属性从当前装备重算', False,
               'Hero 缺少 equip/unequip 接口')
        return
    w1 = Weapon.Weapon(1, 'warrior', 'novice', 'sword', 8, 40, 'none')
    w1.baseatk = w1.atk = 8
    w2 = Weapon.Weapon(1, 'warrior', 'novice', 'sword', 5, 40, 'none')
    w2.baseatk = w2.atk = 5
    hero.equip('weapon', w1)
    after_equip = hero.atk
    hero.equip('weapon', w2)
    after_replace = hero.atk
    hero.applyequip()
    hero.applyequip()
    after_reapply = hero.atk
    hero.unequip('weapon')
    after_unequip = hero.atk
    ok = (after_equip == baseatk + 8 and after_replace == baseatk + 5
          and after_reapply == baseatk + 5 and after_unequip == baseatk)
    report('1b 装备装配/替换/脱卸后属性从当前装备重算', ok,
           'atk equip=%d replace=%d reapply=%d unequip=%d (base=%d)'
           % (after_equip, after_replace, after_reapply, after_unequip, baseatk))


# --- Bug 1c: 护甲破损状态必须反映到英雄属性 ------------------------------------
def check_broken_gear_state():
    hero = make_hero()
    armor = hero.ourarmor
    armor.damagedur(100000, 1)
    dur_ok = armor.dur == 0
    defn_ok = armor.defn == int(armor.basedefn * 0.3)
    hero.applyequip()
    hero_def_ok = hero.defn == int(hero.basedef + armor.defn + hero.ourshield.defn)
    armor.repair()
    hero.applyequip()
    repaired_ok = (armor.dur == armor.maxdur
                   and hero.defn == int(hero.basedef + armor.basedefn + hero.ourshield.defn))
    ok = dur_ok and defn_ok and hero_def_ok and repaired_ok
    report('1c 护甲破损/修复后英雄防御按当前装备重算', ok,
           'dur_ok=%s defn_ok=%s hero_def_ok=%s repaired_ok=%s'
           % (dur_ok, defn_ok, hero_def_ok, repaired_ok))


# --- Bug 2a: 敌人逃跑后不应掉落金币/经验 --------------------------------------
def check_fled_enemy_loot():
    hero = make_hero()
    enemy = Enemy.Enemy(1, 'Timid', 'Corny', 'Duck', 5, 10, 100, 1000, 0, 'run')
    game = make_game(hero, enemy)
    hero.isbattling = True
    with mock.patch('builtins.input', return_value=''), \
            mock.patch('random.randrange', return_value=2), quiet():
        game.battle()  # enemyturn 中 overunder == 2 -> 敌人逃跑
    ok = hero.gold == 0 and hero.xp == 0
    report('2a 敌人逃跑后不掉落金币/经验', ok,
           'got gold=%d xp=%d, expected 0/0' % (hero.gold, hero.xp))


# --- Bug 2b: 死亡敌人不应继续行动 ---------------------------------------------
def check_dead_enemy_no_turn():
    hero = make_hero()
    hero.isbattling = True
    enemy = Enemy.Enemy(1, 'A', 'B', 'C', 50, 0, 0, 10, 0, 'none')
    enemy.hp = 0
    game = make_game(hero, enemy)
    hp_before = hero.hp
    with mock.patch('random.randrange', return_value=10), quiet():
        game.enemyturn()
    ok = hero.hp == hp_before
    report('2b 死亡敌人不再攻击英雄', ok,
           'hero hp %d -> %d' % (hp_before, hero.hp))


# --- Bug 3a: 耐久下限与修理费上限 ---------------------------------------------
def check_durability_bounds():
    shield = Shield.Shield(1, 'warrior', 'weak', 'shield', 3, 30)
    shield.maxdur = shield.dur = 30
    shield.damagedur(100000, 1)
    dur_after_damage = shield.dur
    floor_ok = shield.dur == 0
    repair_cost = shield.maxdur - shield.dur
    cost_ok = 0 <= repair_cost <= shield.maxdur
    shield.restoredur(100000)
    cap_ok = shield.dur == shield.maxdur
    ok = floor_ok and cost_ok and cap_ok
    report('3a 耐久不越界且修理费在 [0, maxdur] 内', ok,
           'dur after damage=%d, repair cost=%d, dur after restore=%d'
           % (dur_after_damage, repair_cost, shield.dur))


# --- Bug 3b: 铁匠铺购买价格不越界 ---------------------------------------------
def check_blacksmith_price_bounds():
    hero = make_hero()
    hero.gold = 0
    game = make_game(hero, None)
    sale = Weapon.Weapon(1, 'warrior', 'novice', 'sword', 8, 40, 'none')
    hero.newweapon = lambda: sale
    hero.newarmor = lambda: hero.ourarmor
    hero.newshield = lambda: hero.ourshield
    old_weapon = hero.ourweapon
    answers = iter(['b', '1'])  # 选购买, 再选武器, 但金币为 0
    with mock.patch('builtins.input', lambda *args: next(answers)), quiet():
        game.blacksmith()
    ok = hero.gold >= 0 and hero.ourweapon is old_weapon
    report('3b 铁匠铺金币不为负、未付款不会给装备', ok,
           'gold=%r, weapon_replaced=%s' % (hero.gold, hero.ourweapon is not old_weapon))


# --- Bug 4: 数据库重建无重复行、失败后无旧表残留 -------------------------------
def check_database_rebuild():
    repo = os.getcwd()
    tmp = tempfile.mkdtemp()
    try:
        os.chdir(tmp)
        os.makedirs('db')
        shutil.copytree(os.path.join(repo, 'csv'), 'csv')
        tables = ['weapons', 'armor', 'shields', 'items', 'enemies', 'levelnotes', 'riddles']
        expected = {}
        for name in tables:
            with open(os.path.join('csv', name + '.csv')) as fin:
                expected[name] = sum(1 for _ in fin)
        from sqlite3 import connect
        with quiet():
            dbsetup.dbsetup().setupdb()
            dbsetup.dbsetup().setupdb()  # 重建, 不应产生重复行
        conn = connect('db/game.db')
        counts = {name: conn.execute('SELECT COUNT(*) FROM ' + name).fetchone()[0]
                  for name in tables}
        conn.close()
        rebuild_ok = counts == expected

        # 初始化中途失败: riddles.csv 缺失 -> 不应留下残表/残文件
        os.remove('csv/riddles.csv')
        with quiet():
            try:
                dbsetup.dbsetup().setupdb()
            except Exception:
                pass
        residue = False
        if os.path.exists('db/game.db'):
            conn = connect('db/game.db')
            residue = bool(conn.execute(
                "SELECT COUNT(*) FROM sqlite_master WHERE type='table'").fetchone()[0])
            conn.close()
        ok = rebuild_ok and not residue
        report('4 数据库重建无重复行、初始化失败无旧表残留', ok,
               'rebuild_counts=%s, stale_tables_after_failure=%s' % (counts, residue))
    finally:
        os.chdir(repo)
        shutil.rmtree(tmp, ignore_errors=True)


# --- Bug 5a: item_management 拒绝非法序号 -------------------------------------
def check_invalid_item_index():
    hero = make_hero()
    hero.items = [Item.Item(1, 1, 'Healing Potion', 40, 8),
                  Item.Item(1, 1, 'Haste Potion', 0, 5)]
    game = make_game(hero, None)
    with mock.patch('builtins.input', return_value='0'), quiet():
        game.item_management()  # 0 -> -1, 旧代码会静默消耗 items[-1]
    ok = len(hero.items) == 2
    report('5a item_management 拒绝非法序号(0/负数)', ok,
           'items left=%d, expected 2' % len(hero.items))


# --- Bug 5b: loadgame 拒绝非法数值 --------------------------------------------
def check_invalid_loadgame_input():
    repo = os.getcwd()
    tmp = tempfile.mkdtemp()
    try:
        os.chdir(tmp)
        os.makedirs('saves')
        shutil.copy(os.path.join(repo, 'saves', 'TESTSAVE1.hero'), 'saves/')
        game = make_game(make_hero(), None)
        crashed = False
        with mock.patch('builtins.input', return_value='abc'), quiet():
            try:
                game.loadgame()
            except ValueError:
                crashed = True
        report('5b loadgame 拒绝非法数值输入而不崩溃', crashed is False,
               'ValueError on input "abc"' if crashed else '')
    finally:
        os.chdir(repo)
        shutil.rmtree(tmp, ignore_errors=True)


# --- 物品堆叠 ----------------------------------------------------------------
def check_item_stacking():
    hero = make_hero()
    if not hasattr(hero, 'additem'):
        report('6 相同物品堆叠、不同物品分列', False, 'Hero 缺少 additem 堆叠接口')
        return
    hero.additem(Item.Item(1, 1, 'Healing Potion', 40, 8))
    hero.additem(Item.Item(1, 1, 'Healing Potion', 40, 8))
    hero.additem(Item.Item(1, 1, 'Haste Potion', 0, 5))
    ok = len(hero.items) == 2 and hero.items[0].count == 2 and hero.items[1].count == 1
    report('6 相同物品堆叠、不同物品分列', ok,
           'inventory=%s' % [(item.name, item.count) for item in hero.items])


def main():
    check_victory_loot_duplication()
    check_equipment_recompute()
    check_broken_gear_state()
    check_fled_enemy_loot()
    check_dead_enemy_no_turn()
    check_durability_bounds()
    check_blacksmith_price_bounds()
    check_database_rebuild()
    check_invalid_item_index()
    check_invalid_loadgame_input()
    check_item_stacking()
    remaining = sum(1 for _, ok in RESULTS if not ok)
    print('\n%d/%d checks passing, %d bug(s) remaining'
          % (len(RESULTS) - remaining, len(RESULTS), remaining))
    return remaining


if __name__ == '__main__':
    sys.exit(1 if main() else 0)
