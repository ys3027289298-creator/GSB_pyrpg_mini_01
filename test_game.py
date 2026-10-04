"""Regression tests for PyRPG_Mini.

Covers equipment equip/unequip, item stacking, and database rebuild.
The core invariant: after every operation, hero stats are recomputed
from the CURRENT equipment (atk = baseatk + weapon.atk,
defn = basedef + armor.defn + shield.defn) with no duplicate bonuses.

Run from the repo root:  python3 -m unittest test_game -v
"""
import csv
import io
import os
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

import Armor
import Enemy
import Game
import Hero
import Item
import Shield
import Weapon
import dbsetup


def quiet(fn, *args, **kwargs):
    """Run a function with stdout swallowed (game prints a lot)."""
    with redirect_stdout(io.StringIO()):
        return fn(*args, **kwargs)


def make_game():
    """Create a Game, answering its startup prompts with defaults."""
    with patch('builtins.input', return_value=''), redirect_stdout(io.StringIO()):
        return Game.Game()


def make_hero():
    """Warrior with forced 'Common' gear quality for deterministic stats."""
    with patch('random.randint', return_value=50):
        hero = Hero.Hero('warrior', 1, 20, 7, 3, 11, 0)
    quiet(hero.heroperks)
    hero.defcurve = .1
    hero.atkcurve = .1
    return hero


def make_weapon(atk=10, dur=30):
    with patch('random.randint', return_value=50):
        return Weapon.Weapon(1, 'warrior', 'test', 'sword', atk, dur, 'none')


def make_armor(defn=5, dur=30):
    with patch('random.randint', return_value=50):
        return Armor.Armor(1, 'warrior', 'test', 'plate', defn, dur)


def make_shield(defn=4, dur=30):
    with patch('random.randint', return_value=50):
        return Shield.Shield(1, 'warrior', 'test', 'shield', defn, dur)


def csv_row_count(path):
    with open(path, 'r') as fin:
        return sum(1 for row in csv.reader(fin) if row)


def setUpModule():
    # guarantee a clean, fully built database before any test runs
    quiet(dbsetup.dbsetup().setupdb)


class TestEquipUnequip(unittest.TestCase):
    def setUp(self):
        self.hero = make_hero()

    def assert_stats_match_gear(self, hero):
        self.assertEqual(hero.atk, int(hero.baseatk + hero.ourweapon.atk))
        self.assertEqual(hero.defn, int(hero.basedef + hero.ourarmor.defn + hero.ourshield.defn))

    def test_equip_sets_stats_from_current_gear(self):
        self.hero.equip('weapon', make_weapon(atk=10))
        self.hero.equip('armor', make_armor(defn=5))
        self.hero.equip('shield', make_shield(defn=4))
        self.assertEqual(self.hero.atk, self.hero.baseatk + 10)
        self.assertEqual(self.hero.defn, self.hero.basedef + 5 + 4)
        self.assert_stats_match_gear(self.hero)

    def test_replacement_never_duplicates_bonus(self):
        self.hero.equip('weapon', make_weapon(atk=10))
        first_atk = self.hero.atk
        # replace the weapon several times, including re-equipping the first one
        self.hero.equip('weapon', make_weapon(atk=20))
        self.assertEqual(self.hero.atk, self.hero.baseatk + 20)
        self.hero.equip('weapon', make_weapon(atk=10))
        self.assertEqual(self.hero.atk, first_atk)
        for _ in range(3):
            self.hero.equip('armor', make_armor(defn=5))
            self.hero.equip('shield', make_shield(defn=4))
            self.hero.applyequip()
            self.assertEqual(self.hero.defn, self.hero.basedef + 5 + 4)
            self.assert_stats_match_gear(self.hero)

    def test_unequip_restores_training_baseline(self):
        self.hero.applyequip()
        baseline = (self.hero.atk, self.hero.defn)
        self.hero.equip('weapon', make_weapon(atk=99))
        self.hero.equip('armor', make_armor(defn=99))
        self.hero.equip('shield', make_shield(defn=99))
        self.hero.unequip('weapon')
        self.hero.unequip('armor')
        self.hero.unequip('shield')
        self.assertEqual((self.hero.atk, self.hero.defn), baseline)
        self.assert_stats_match_gear(self.hero)

    def test_unknown_slot_rejected(self):
        with self.assertRaises(ValueError):
            self.hero.equip('ring', make_weapon())
        with self.assertRaises(ValueError):
            self.hero.unequip('ring')

    def test_broken_gear_lowers_hero_stats(self):
        weapon = make_weapon(atk=10)
        self.hero.equip('weapon', weapon)
        weapon.dur = 1
        weapon.damagedur(100, .5)  # breaks the weapon
        self.assertTrue(weapon.isbroken())
        self.assertEqual(weapon.atk, int(10 * .3))
        self.hero.applyequip()
        self.assertEqual(self.hero.atk, self.hero.baseatk + weapon.atk)
        # broken armor reduces defn (and must not grow a bogus 'atk' attribute)
        armor = make_armor(defn=5)
        self.hero.equip('armor', armor)
        armor.dur = 0
        armor.damagedur(1, .1)
        self.assertFalse(hasattr(armor, 'atk'))
        self.assertEqual(armor.defn, int(5 * .3))
        self.hero.applyequip()
        self.assert_stats_match_gear(self.hero)

    def test_durability_stays_in_bounds(self):
        armor = make_armor(dur=30)
        armor.dur = 2
        armor.damagedur(1000, .5)
        self.assertEqual(armor.dur, 0)
        armor.restoredur(9999)
        self.assertEqual(armor.dur, armor.maxdur)
        weapon = make_weapon(dur=30)
        weapon.dur = 5
        weapon.restoredur(1)
        self.assertEqual(weapon.dur, 6)  # restores by amount, not to max
        shield = make_shield(dur=30)
        shield.dur = 1
        shield.damagedur(1000, .5)
        self.assertEqual(shield.dur, 0)


class TestItemStacking(unittest.TestCase):
    def setUp(self):
        self.hero = make_hero()

    def potion(self, name='Healing Potion', effect=10, val=8):
        return Item.Item(1, 'minor', name, effect, val)

    def test_additem_stacks_by_name(self):
        self.hero.additem(self.potion())
        self.hero.additem(self.potion())
        self.hero.additem(self.potion())
        self.assertEqual(len(self.hero.items), 1)
        self.assertEqual(self.hero.items[0].count, 3)
        self.hero.additem(self.potion(name='Haste Potion', effect=0))
        self.assertEqual(len(self.hero.items), 2)

    def test_buyitem_stacks_and_charges_price(self):
        self.hero.gold = 100
        quiet(self.hero.buyitem, self.potion())
        quiet(self.hero.buyitem, self.potion())
        self.assertEqual(self.hero.gold, 100 - 2 * 8)
        self.assertEqual(len(self.hero.items), 1)
        self.assertEqual(self.hero.items[0].count, 2)

    def test_using_item_consumes_one_from_stack(self):
        game = make_game()
        self.hero.hp = 5
        self.hero.items = [self.potion(effect=10), self.potion(effect=10)]
        self.hero.items[0].count = 2
        del self.hero.items[1]
        game.ourhero = self.hero
        with patch('builtins.input', return_value='1'):
            quiet(game.item_management)
        self.assertEqual(self.hero.hp, 15)
        self.assertEqual(len(self.hero.items), 1)
        self.assertEqual(self.hero.items[0].count, 1)
        # use the last one: stack is removed from the inventory
        with patch('builtins.input', return_value='1'):
            quiet(game.item_management)
        self.assertEqual(len(self.hero.items), 0)

    def test_invalid_item_choice_consumes_nothing(self):
        game = make_game()
        self.hero.items = [self.potion(), self.potion(name='Haste Potion', effect=0)]
        game.ourhero = self.hero
        for bad_input in ('0', 'abc', '99', '-1'):
            with patch('builtins.input', return_value=bad_input):
                result = quiet(game.item_management)
            self.assertFalse(result)
            self.assertEqual(len(self.hero.items), 2)


class TestDatabaseRebuild(unittest.TestCase):
    def table_count(self, table):
        db = dbsetup.dbsetup()
        db.conn.execute('SELECT COUNT(*) FROM ' + table)
        count = db.conn.fetchone()[0]
        db.conn.close()
        return count

    def test_rebuild_is_idempotent(self):
        quiet(dbsetup.dbsetup().setupdb)
        quiet(dbsetup.dbsetup().setupdb)  # rebuild again
        for table, _, csvpath in dbsetup.dbsetup.TABLES:
            self.assertEqual(self.table_count(table), csv_row_count(csvpath),
                             'table %s must match its csv after rebuild' % table)

    def test_failed_rebuild_keeps_previous_tables(self):
        quiet(dbsetup.dbsetup().setupdb)
        before = {table: self.table_count(table) for table, _, _ in dbsetup.dbsetup.TABLES}
        # sabotage one csv so setup fails halfway through
        os.rename('./csv/weapons.csv', './csv/weapons.csv.bak')
        try:
            with self.assertRaises(Exception):
                quiet(dbsetup.dbsetup().setupdb)
        finally:
            os.rename('./csv/weapons.csv.bak', './csv/weapons.csv')
        # transaction rolled back: every table still holds the old data
        after = {table: self.table_count(table) for table, _, _ in dbsetup.dbsetup.TABLES}
        self.assertEqual(before, after)
        quiet(dbsetup.dbsetup().setupdb)  # leave a clean db behind


class TestBattleLoot(unittest.TestCase):
    def setUp(self):
        self.game = make_game()
        self.hero = make_hero()
        self.hero.name = 'Tester'
        self.game.ourhero = self.hero
        self.hero.isbattling = True

    def test_fled_enemy_drops_no_loot(self):
        self.game.ourenemy = Enemy.Enemy(1, 'Timid', 'Corny', 'Duck', 1, 10, 100, 500, 0, '')
        # crit roll 50 (no crit), enemy roll 2 (enemy runs away)
        with patch('builtins.input', return_value='a'), \
                patch('random.randrange', side_effect=[50, 2, 50, 50]):
            quiet(self.game.battle)
        self.assertEqual(self.hero.gold, 0)
        self.assertEqual(self.hero.xp, 0)
        self.assertFalse(self.hero.isbattling)
        # enemy object is reusable afterwards
        self.assertFalse(self.game.ourenemy.fled)
        self.assertEqual(self.game.ourenemy.hp, self.game.ourenemy.maxhp)

    def test_slain_enemy_drops_loot_once(self):
        self.hero.equip('weapon', make_weapon(atk=500))
        self.game.ourenemy = Enemy.Enemy(1, 'Timid', 'Corny', 'Duck', 1, 1, 100, 10, 0, '')
        with patch('builtins.input', return_value='a'), \
                patch('random.randrange', side_effect=[50, 50, 50]):
            quiet(self.game.battle)
        self.assertGreater(self.hero.gold, 0)
        self.assertGreater(self.hero.xp, 0)
        self.assertFalse(self.hero.isbattling)


class TestInputValidation(unittest.TestCase):
    def setUp(self):
        self.game = make_game()
        self.hero = make_hero()
        self.game.ourhero = self.hero

    def test_loadgame_rejects_invalid_index(self):
        for bad_input in ('abc', '99', '-1', '1.5'):
            with patch('builtins.input', return_value=bad_input):
                result = quiet(self.game.loadgame)
            self.assertIs(result, self.hero)

    def test_newhero_reprompts_until_valid_class(self):
        with patch('builtins.input', side_effect=['x', 'w', '1', 'Testname']):
            hero = quiet(self.game.newhero)
        self.assertEqual(hero.ourclass, 'warrior')
        self.assertEqual(hero.name, 'Testname')


class TestBlacksmithPrices(unittest.TestCase):
    def setUp(self):
        self.game = make_game()
        self.hero = make_hero()
        self.game.ourhero = self.hero

    def test_cannot_buy_without_gold(self):
        self.hero.gold = 0
        old_weapon = self.hero.ourweapon
        with patch('builtins.input', side_effect=['b', '1']):
            quiet(self.game.blacksmith)
        self.assertEqual(self.hero.gold, 0)
        self.assertIs(self.hero.ourweapon, old_weapon)

    def test_buy_charges_exact_price_and_equips(self):
        self.hero.gold = 1000
        self.hero.defcurve = .1  # cost = int(level * 60 * .1) = 6 at level 1
        with patch('builtins.input', side_effect=['b', '2']):
            quiet(self.game.blacksmith)
        self.assertEqual(self.hero.gold, 1000 - 6)
        # stats recomputed from the newly equipped shield, no stacking
        self.assertEqual(self.hero.defn,
                         int(self.hero.basedef + self.hero.ourarmor.defn + self.hero.ourshield.defn))


if __name__ == '__main__':
    unittest.main()
