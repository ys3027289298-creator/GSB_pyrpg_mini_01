import csv
import os
from sqlite3 import connect

import Game


class dbsetup():
    # (table name, column definitions, csv source) for every game table
    TABLES = [
        ('armor',
         '(level INTEGER, class TEXT, name TEXT, type TEXT, basedef INTEGER, durability INTEGER)',
         './csv/armor.csv'),
        ('enemies',
         '(level INT, firstname TEXT, middlename TEXT, lastname TEXT, attack INTEGER, xp INTEGER, gold INTEGER, hp INTEGER, def INTEGER, status TEXT)',
         './csv/enemies.csv'),
        ('items',
         '(level INT, grade INT,name TEXT,effect INT,value INT)',
         './csv/items.csv'),
        ('levelnotes',
         '(Level INT,HP INT,ATK INT,DEF INT,xptonextlevel INT, dodge INT )',
         './csv/levelnotes.csv'),
        ('shields',
         '(level INT,class TEXT,name TEXT,type TEXT,basedef INT,durability INT)',
         './csv/shields.csv'),
        ('weapons',
         '( level INTEGER ,class TEXT ,name TEXT ,type TEXT,baseattack INTEGER ,durability INTEGER ,power TEXT)',
         './csv/weapons.csv'),
        ('riddles',
         '(question TEXT ,answer TEXT)',
         './csv/riddles.csv'),
    ]

    def __init__(self):
        self.dbpath = './db/game.db'
        # import and create our player database
        self.gamedb = connect(self.dbpath)
        self.conn = self.gamedb.cursor()

    # used to delete the current database
    def deletedbifexists(self):
        if os.path.exists('./db/game.db'):
            os.remove('./db/game.db')

    def setupdb(self):
        # If you set this to 1, it will print out all data as it populates the datbase.
        debugging = 0

        # make a database connection to the game database
        if debugging:
            print('connecting to database')
        conn = connect('./db/game.db')

        # create our cursor
        if debugging:
            print('creating cursor')
        cur = conn.cursor()

        try:
            # one explicit transaction: sqlite3 autocommits DDL otherwise,
            # which would strand dropped tables if a csv fails to load
            cur.execute('BEGIN')
            for table, columns, csvpath in self.TABLES:
                # drop the old table so a rebuild never leaves stale rows behind
                if debugging:
                    print('dropping old table ' + table)
                cur.execute('DROP TABLE IF EXISTS ' + table)

                # create our table in the database
                if debugging:
                    print('creating table for ' + table)
                cur.execute('CREATE TABLE ' + table + ' ' + columns)

                # insert our csv data into the table
                if debugging:
                    print('inserting ' + table + ' into database')
                with open(csvpath, 'r') as fin:
                    dr = csv.reader(fin)
                    for i in dr:
                        if not i:
                            continue
                        if debugging:
                            print('inserting ' + str(i))
                        cur.execute('INSERT INTO ' + table + ' VALUES (' + ','.join('?' * len(i)) + ');', i)
                if debugging:
                    cur.execute('SELECT * FROM ' + table)
                    rows = cur.fetchall()
                    for row in rows:
                        print('QUERY ALL: ' + str(row))
            # commit the changes
            conn.commit()
        except Exception:
            # roll back so a failed setup never leaves half-built tables behind
            conn.rollback()
            conn.close()
            raise
        # close the database connection to let other operations use it
        conn.close()
        Game.centerprint('...Have fun')
