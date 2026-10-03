#!/usr/bin/env python3
"""PyRPG_Mini 回归测试。

覆盖: 装备装配/替换/脱卸、物品堆叠、数据库重建、战斗结算、
耐久与价格边界、非法输入。核心不变量: 每次操作后英雄属性都从
当前装备重新计算, 无重复加成。

运行: python3 -m unittest test_pyrpg -v
"""

import contextlib
import io
import os
import shutil
import tempfile
import unittest
from sqlite3 import connect
from unittest import mock

import Armor
import Enemy
import Game
import Hero
import Item
import Shield
import Weapon
import dbsetup


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


def make_weapon(atk):
    weapon = Weapon.Weapon(1, 'warrior', 'novice', 'sword', atk, 40, 'none')
    weapon.baseatk = weapon.atk = atk
    weapon.maxdur = weapon.dur = 40
    return weapon


def make_armor(defn):
    armor = Armor.Armor(1, 'warrior', 'weak', 'plate', defn, 30)
    armor.basedefn = armor.defn = defn
    armor.maxdur = armor.dur = 30
    return armor


def make_shield(defn):
    shield = Shield.Shield(1, 'warrior', 'weak', 'shield', defn, 30)
    shield.basedefn = shield.defn = defn
    shield.maxdur = shield.dur = 30
    return shield


def make_game(hero, enemy=None):
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


class TestEquipment(unittest.TestCase):
    """装备装配、替换、脱卸后属性都从当前装备重新计算。"""

    def test_equip_recomputes_stats(self):
        hero = make_hero()
        hero.equip('weapon', make_weapon(8))
        self.assertEqual(hero.atk, hero.baseatk + 8)
        hero.equip('armor', make_armor(4))
        hero.equip('shield', make_shield(5))
        self.assertEqual(hero.defn, hero.basedef + 4 + 5)

    def test_replacement_has_no_duplicate_bonus(self):
        hero = make_hero()
        hero.equip('weapon', make_weapon(8))
        hero.equip('weapon', make_weapon(5))
        self.assertEqual(hero.atk, hero.baseatk + 5)
        hero.equip('armor', make_armor(4))
        hero.equip('armor', make_armor(6))
        self.assertEqual(hero.defn, hero.basedef + 6 + hero.ourshield.defn)

    def test_unequip_returns_to_base(self):
        hero = make_hero()
        hero.equip('weapon', make_weapon(8))
        hero.unequip('weapon')
        self.assertIsNone(hero.ourweapon)
        self.assertEqual(hero.atk, hero.baseatk)
        hero.unequip('armor')
        hero.unequip('shield')
        self.assertEqual(hero.defn, hero.basedef)

    def test_applyequip_is_idempotent(self):
        hero = make_hero()
        hero.equip('weapon', make_weapon(8))
        for _ in range(5):
            hero.applyequip()
        self.assertEqual(hero.atk, hero.baseatk + 8)
        self.assertEqual(hero.defn, hero.basedef + 2 + 3)

    def test_unknown_slot_rejected(self):
        hero = make_hero()
        with self.assertRaises(ValueError):
            hero.equip('ring', make_weapon(8))

    def test_broken_gear_lowers_stats_and_repair_restores(self):
        hero = make_hero()
        hero.ourarmor.damagedur(100000, 1)
        self.assertEqual(hero.ourarmor.dur, 0)
        self.assertEqual(hero.ourarmor.defn, int(hero.ourarmor.basedefn * 0.3))
        hero.applyequip()
        self.assertEqual(hero.defn, int(hero.basedef + hero.ourarmor.defn + 3))
        hero.ourarmor.repair()
        hero.applyequip()
        self.assertEqual(hero.defn, hero.basedef + 2 + 3)


class TestDurabilityBounds(unittest.TestCase):
    """耐久与修理费不越界。"""

    def test_durability_never_negative(self):
        for gear in (make_weapon(8), make_armor(4), make_shield(3)):
            gear.damagedur(100000, 1)
            self.assertEqual(gear.dur, 0)
            self.assertTrue(gear.isbroken())

    def test_restore_never_exceeds_max(self):
        for gear in (make_weapon(8), make_armor(4), make_shield(3)):
            gear.damagedur(10, 1)
            gear.restoredur(100000)
            self.assertEqual(gear.dur, gear.maxdur)

    def test_repair_cost_within_bounds(self):
        shield = make_shield(3)
        shield.damagedur(100000, 1)
        cost = shield.maxdur - shield.dur
        self.assertGreaterEqual(cost, 0)
        self.assertLessEqual(cost, shield.maxdur)


class TestItemStacking(unittest.TestCase):
    """物品堆叠与消耗。"""

    def test_identical_items_stack(self):
        hero = make_hero()
        hero.additem(Item.Item(1, 1, 'Healing Potion', 40, 8))
        hero.additem(Item.Item(1, 1, 'Healing Potion', 40, 8))
        self.assertEqual(len(hero.items), 1)
        self.assertEqual(hero.items[0].count, 2)

    def test_different_items_do_not_stack(self):
        hero = make_hero()
        hero.additem(Item.Item(1, 1, 'Healing Potion', 40, 8))
        hero.additem(Item.Item(1, 1, 'Haste Potion', 0, 5))
        self.assertEqual(len(hero.items), 2)

    def test_using_item_decrements_stack(self):
        hero = make_hero()
        hero.hp = 1
        hero.additem(Item.Item(1, 1, 'Healing Potion', 40, 8))
        hero.additem(Item.Item(1, 1, 'Healing Potion', 40, 8))
        game = make_game(hero)
        with mock.patch('builtins.input', return_value='1'), quiet():
            game.item_management()
        self.assertEqual(len(hero.items), 1)
        self.assertEqual(hero.items[0].count, 1)
        self.assertGreater(hero.hp, 1)

    def test_buyitem_stacks_and_charges_price(self):
        hero = make_hero()
        hero.gold = 100
        hero.buyitem(Item.Item(1, 1, 'Healing Potion', 40, 8), 12)
        hero.buyitem(Item.Item(1, 1, 'Healing Potion', 40, 8), 12)
        self.assertEqual(hero.gold, 76)
        self.assertEqual(hero.items[0].count, 2)

    def test_stacking_does_not_change_stats(self):
        hero = make_hero()
        atk, defn = hero.atk, hero.defn
        hero.additem(Item.Item(1, 1, 'Healing Potion', 40, 8))
        hero.additem(Item.Item(1, 1, 'Healing Potion', 40, 8))
        self.assertEqual((hero.atk, hero.defn), (atk, defn))


class TestInvalidInput(unittest.TestCase):
    """非法数值输入不会导致计算异常。"""

    def test_item_management_rejects_bad_index(self):
        for bad in ('0', '-1', '99', 'abc'):
            hero = make_hero()
            hero.items = [Item.Item(1, 1, 'Healing Potion', 40, 8),
                          Item.Item(1, 1, 'Haste Potion', 0, 5)]
            game = make_game(hero)
            with mock.patch('builtins.input', return_value=bad), quiet():
                game.item_management()
            self.assertEqual(len(hero.items), 2, 'input %r consumed an item' % bad)

    def test_loadgame_rejects_bad_index(self):
        repo = os.getcwd()
        tmp = tempfile.mkdtemp()
        try:
            os.chdir(tmp)
            os.makedirs('saves')
            shutil.copy(os.path.join(repo, 'saves', 'TESTSAVE1.hero'), 'saves/')
            game = make_game(make_hero())
            for bad in ('abc', '9', '-1'):
                with mock.patch('builtins.input', return_value=bad), quiet():
                    self.assertIsNone(game.loadgame())
        finally:
            os.chdir(repo)
            shutil.rmtree(tmp, ignore_errors=True)


class TestBattleFlow(unittest.TestCase):
    """按 Game 回合流程验证结算与掉落。"""

    def run_battle(self, hero, enemy, randrange=None):
        game = make_game(hero, enemy)
        hero.isbattling = True
        with contextlib.ExitStack() as stack:
            stack.enter_context(mock.patch('builtins.input', return_value=''))
            stack.enter_context(quiet())
            if randrange is not None:
                stack.enter_context(mock.patch('random.randrange', return_value=randrange))
            game.battle()
        return game

    def test_kill_awards_loot_exactly_once(self):
        hero = make_hero()
        enemy = Enemy.Enemy(1, 'Timid', 'Corny', 'Duck', 5, 10, 100, 1, 0, 'none')
        self.run_battle(hero, enemy)
        self.assertEqual(hero.gold, int(100 + 100 * hero.defcurve))
        self.assertEqual(hero.xp, int(10 + 10 * hero.defcurve))

    def test_fled_enemy_drops_nothing(self):
        hero = make_hero()
        enemy = Enemy.Enemy(1, 'Timid', 'Corny', 'Duck', 5, 10, 100, 1000, 0, 'run')
        self.run_battle(hero, enemy, randrange=2)  # enemyturn 掷出 2 -> 逃跑
        self.assertEqual(hero.gold, 0)
        self.assertEqual(hero.xp, 0)

    def test_dead_enemy_takes_no_turn(self):
        hero = make_hero()
        hero.isbattling = True
        enemy = Enemy.Enemy(1, 'A', 'B', 'C', 50, 0, 0, 10, 0, 'none')
        enemy.hp = 0
        game = make_game(hero, enemy)
        with mock.patch('random.randrange', return_value=10), quiet():
            game.enemyturn()
        self.assertEqual(hero.hp, hero.maxhp)

    def test_enemy_reset_clears_fled_state(self):
        enemy = Enemy.Enemy(1, 'A', 'B', 'C', 5, 0, 0, 10, 0, 'run')
        enemy.run()
        self.assertTrue(enemy.hasrun)
        enemy.reset()
        self.assertFalse(enemy.hasrun)
        self.assertEqual(enemy.hp, enemy.maxhp)


class TestBlacksmith(unittest.TestCase):
    """铁匠铺价格与修理不越界。"""

    def make_shop_game(self, gold):
        hero = make_hero()
        hero.gold = gold
        hero.newweapon = lambda: make_weapon(8)
        hero.newarmor = lambda: make_armor(4)
        hero.newshield = lambda: make_shield(3)
        return hero, make_game(hero)

    def test_cannot_buy_without_gold(self):
        hero, game = self.make_shop_game(0)
        old_weapon = hero.ourweapon
        answers = iter(['b', '1'])
        with mock.patch('builtins.input', lambda *args: next(answers)), quiet():
            game.blacksmith()
        self.assertEqual(hero.gold, 0)
        self.assertIs(hero.ourweapon, old_weapon)

    def test_buy_charges_correct_slot_price(self):
        hero, game = self.make_shop_game(1000)
        answers = iter(['b', '3'])  # 买护甲
        with mock.patch('builtins.input', lambda *args: next(answers)), quiet():
            game.blacksmith()
        self.assertEqual(hero.gold, 1000 - int(1 * 60 * hero.defcurve))
        self.assertEqual(hero.defn, hero.basedef + 4 + hero.ourshield.defn)

    def test_repair_restores_stats(self):
        hero, game = self.make_shop_game(1000)
        hero.ourweapon.dur = 0
        hero.ourweapon.gearbreak()
        hero.applyequip()
        answers = iter(['f', '1', 'y'])
        with mock.patch('builtins.input', lambda *args: next(answers)), quiet():
            game.blacksmith()
        self.assertEqual(hero.ourweapon.dur, hero.ourweapon.maxdur)
        self.assertEqual(hero.atk, hero.baseatk + hero.ourweapon.baseatk)


class TestDatabaseRebuild(unittest.TestCase):
    """数据库重建无重复行、无旧表残留、失败不留残库。"""

    TABLES = ['weapons', 'armor', 'shields', 'items', 'enemies', 'levelnotes', 'riddles']

    def setUp(self):
        self.repo = os.getcwd()
        self.tmp = tempfile.mkdtemp()
        os.chdir(self.tmp)
        os.makedirs('db')
        shutil.copytree(os.path.join(self.repo, 'csv'), 'csv')

    def tearDown(self):
        os.chdir(self.repo)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def rowcounts(self):
        conn = connect('db/game.db')
        counts = {name: conn.execute('SELECT COUNT(*) FROM ' + name).fetchone()[0]
                  for name in self.TABLES}
        conn.close()
        return counts

    def expected_counts(self):
        expected = {}
        for name in self.TABLES:
            with open(os.path.join('csv', name + '.csv')) as fin:
                expected[name] = sum(1 for _ in fin)
        return expected

    def test_rebuild_has_no_duplicate_rows(self):
        with quiet():
            dbsetup.dbsetup().setupdb()
            dbsetup.dbsetup().setupdb()
        self.assertEqual(self.rowcounts(), self.expected_counts())

    def test_rebuild_drops_stale_rows(self):
        with quiet():
            dbsetup.dbsetup().setupdb()
        conn = connect('db/game.db')
        conn.execute("INSERT INTO weapons VALUES (99, 'x', 'stale', 'x', 1, 1, 'x')")
        conn.commit()
        conn.close()
        with quiet():
            dbsetup.dbsetup().setupdb()
        conn = connect('db/game.db')
        stale = conn.execute("SELECT COUNT(*) FROM weapons WHERE name = 'stale'").fetchone()[0]
        conn.close()
        self.assertEqual(stale, 0)
        self.assertEqual(self.rowcounts(), self.expected_counts())

    def test_failed_setup_leaves_no_residue(self):
        os.remove('csv/riddles.csv')
        with quiet():
            self.assertRaises(Exception, dbsetup.dbsetup().setupdb)
        self.assertFalse(os.path.exists('db/game.db'))


if __name__ == '__main__':
    unittest.main()
