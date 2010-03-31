#first we have to monkey-patch standard library to support green threads
import eventlet
eventlet.monkey_patch()

import datetime
import hashlib
import os
import random
import re
import shlex
from struct import *
import uuid

from pkg import bc2_misc
from eventlet.green import socket
from eventlet.green import time
import mysql.connector

############################################################
#Consumer loops
############################################################
def log_processor():
    global log_q
    screen_log("Database logger access thread started", 2)
    #Make sure our tables exist
    create_tables()

    cfg = _get_mysql_config()
    db = mysql.connector.Connect(host=cfg['host'],
                             user=cfg['user'],
                             password=cfg['password'],
                             database=cfg['database'])
    cursor = db.cursor()

    while True:
        #loop waiting for log messages
        try:
            msg = log_q.get()
        except:
            continue

        screen_log("LOGGING to bc2_btpslog: " % msg, 2)
        dt = datetime.datetime.today().strftime("%m/%d/%y %H:%M:%S")
        stmt_insert = "INSERT INTO bc2_btpslog (dt, message) VALUES (%s, %s)"
        try:
            cursor.execute(stmt_insert, (dt, msg))
            db.commit()
        except:
            screen_log("ERROR LOGGING to bc2_btpslog!!!", 1)

def command_processor():
    ''' Loops waiting for commands to be in command_q and then sends them to
        the server.
    '''
    global command_socket
    screen_log("Command processor thread started", 2)
    while True:
        try:
            cmd = command_q.get()
        except:
            continue
        cmd = cmd[1]
        _send_command(command_socket, cmd)

def event_logger():
    ''' Loops waiting for events to be in event_log_q and then logs them to
        mysql.
    '''
    global event_log_q
    screen_log("Event logger thread started", 2)

    #Make sure our tables exist
    create_tables()

    cfg = _get_mysql_config()
    db = mysql.connector.Connect(host=cfg['host'],
                             user=cfg['user'],
                             password=cfg['password'],
                             database=cfg['database'])

    cursor = db.cursor()

    while True:
        #loop waiting for events
        try:
            evt = event_log_q.get()
        except:
            continue

        recv_time = evt[1].strftime("%Y-%m-%d %H:%M:%S")

        #we only care about [0] from now on
        evt = evt[0]

        if evt[0] == 'player.onChat':
            #log chat
            screen_log("player.onChat - %s: %s" % (evt[1], evt[2]), 2)
            stmt_insert = "INSERT INTO bc2_chat (dt, player, chat) VALUES (%s, %s, %s)"
            try:
                cursor.execute(stmt_insert, (recv_time, evt[1], evt[2]))
                db.commit()
            except:
                log_q.put("Error executing: %s" % stmt_insert % (recv_time, evt[1], evt[2]))


        elif evt[0] == 'player.onJoin':
            #Do things for when player joins server
            stmt_insert = "INSERT INTO bc2_connections (player, jointime) VALUES (%s, %s)"
            if evt[1] == '':
                #sometimes this event returns '', so use alt-method of guessing players name
                player = find_blank_playername(cursor)
                screen_log("player.onJoin - %s" % player, 2)
                try:
                    cursor.execute(stmt_insert, (player, recv_time))
                    db.commit()
                    log_q.put("Found blank playername: %s" % player)
                except:
                    log_q.put("Error executing: %s" % stmt_insert % (player, recv_time))
            else:
                #log new connection
                screen_log("player.onJoin - %s" % evt[1], 2)
                try:
                    cursor.execute(stmt_insert, (evt[1], recv_time))
                    db.commit()
                except:
                    log_q.put("Error executing: %s" % stmt_insert % (evt[1], recv_time))


        elif evt[0] == 'player.onLeave':
            #Do things for when player leaves server
            screen_log("player.onLeave - %s" % evt[1], 2)
            #log disconnect
            stmt_update = """UPDATE bc2_connections
                                SET leavetime=%s
                                WHERE player=%s
                                AND leavetime is NULL
                                AND %s > jointime"""
            try:
                cursor.execute(stmt_update, (recv_time, evt[1], recv_time))
                db.commit()
            except:
                log_q.put("Error executing: %s" % stmt_update % (recv_time, evt[1], recv_time))


        elif evt[0] == 'player.onKill':
            if display_kills:
                screen_log("player.onKill - %s killed %s" % (evt[1], evt[2]), 3)
            stmt_insert = "INSERT INTO bc2_kills (dt, victim, killer) VALUES (%s, %s, %s)"
            try:
                cursor.execute(stmt_insert, (recv_time, evt[2], evt[1]))
                db.commit()
            except:
                log_q.put("Error executing: %s" % stmt_insert % (recv_time, evt[2], evt[1]))


        elif evt[0] == 'punkBuster.onMessage':
            screen_log(r"punkBuster.onMessage: %s" % evt[1], 2)
            if is_pb_new_connection(evt[1]):
                try:
                    name, ip = parse_pb_new_connection(evt[1])
                    stmt_update = "UPDATE bc2_connections SET ip=%s WHERE player=%s AND leavetime IS NULL"
                    cursor.execute(stmt_update, (ip, name))
                    db.commit()
                except:
                    screen_log("ERROR:  Can't parse punkbuster lost connection msg.", 1)

            elif is_pb_lost_connection(evt[1]):
                try:
                    name, pb_guid = parse_pb_lost_connection(evt[1])
                    stmt_update = "UPDATE bc2_connections SET pb_guid=%s WHERE player=%s and pb_guid IS NULL"
                    cursor.execute(stmt_update, (pb_guid, name))
                    db.commit()
                except:
                    screen_log("ERROR:  Can't parse punkbuster lost connection msg.", 1)

            #log punkbuster messages
            stmt_insert = "INSERT INTO bc2_punkbuster (dt, punkbuster) VALUES (%s, %s)"
            try:
                cursor.execute(stmt_insert, (recv_time, evt[1]))
                db.commit()
            except:
                log_q.put("Error executing: %s" % stmt_insert % (recv_time, evt[1]))
        else:
            pass


############################################################
#Command functions
############################################################
def serveryell(admin, words, skt):
    ''' Command action.
        Words should be a list formatted like:
            duration included:
                ["!serveryell", seconds, "message"]
            duration not included:
                ["!serveryell", "message"]

        If duration isn't included we default to 4 seconds.
    '''
    if len(words) < 2:
        playeryell(admin, ['!playeryell', admin, 'ADMIN: Must specify text to yell.'], skt)
        return

    if admin not in admins:
        return

    #check for included duration
    try:
        seconds = int(words[1])
        no_duration = False

    #no duration.  Default to 4 seconds.
    except:
        seconds = 4
        no_duration = True

    #Get message to send from words
    if no_duration:
        msg = words[1]
    else:
        msg = words[2]

    #Build command.  Duration is in ms
    cmd = 'admin.yell "%s" %s all' % (msg, seconds*1000)

    #insert command into command queue
    command_qer(cmd, prio=1)

def playeryell(admin, words, skt):
    ''' Command action.
        Words should be a list formatted like:
            duration included:
                ["!playeryell", seconds, "playername", "message"]
            duration not included:
                ["!playeryell", "playername", "message"]

        If duration isn't included we default to 4 seconds.
    '''
    global command_q

    if admin not in admins:
        return

    #check for included duration
    try:
        seconds = int(words[1])
        no_duration = False

    #no duration.  Default to 4 seconds.
    except:
        seconds = 4
        no_duration = True

    if no_duration:
        player = words[1]
        msg = words[2]
    else:
        player = words[2]
        msg = words[3]

    player_name = select_player(player, get_players_names(skt), admin, skt)

    if player_name == 1:
        return
    elif player_name == 2:
        return
    else:
        cmd = 'admin.yell "%s" %s player %s' % (msg, seconds*1000, player_name)

        #insert command into command queue
        command_qer(cmd, prio=1)

def gonext(admin, words, skt):
    ''' Command action.
        Words should be a list formatted like:
            ["!gonext"]
    '''
    if admin not in admins:
        return

    cmd = 'admin.runNextLevel'

    command_qer(cmd, prio=1)

def map_(player, words, skt):
    ''' Command action.
        Words should be a list formatted like:
            duration included:
                ["!playeryell", seconds, "playername", "message"]
            duration not included:
                ["!playeryell", "playername", "message"]

        If duration isn't included we default to 4 seconds.
    '''
    #global command_q
    message_duration = 8000 #ms
    level = get_map(skt)

    cmd = 'admin.yell "%s" %s player %s' % (level, message_duration, player)
    command_qer(cmd)

def kick(admin, words, skt):
    global command_q

    if admin not in admins:
        return

    _players = get_players_names(skt)

    player_name = select_player(words[1], _players, admin, skt)

    if player_name == 1:
        return
    elif player_name == 2:
        return
    else:
        if player_name not in admins:
            punkb = _get_var(vars.punkBuster, skt)
            print "********************: " + punkb
            if _get_var('vars.punkBuster', skt) == 'false':
                cmd = 'admin.kickPlayer %s' % player_name
                command_qer(cmd, prio=0)
                playeryell(admin, ['!playeryell', admin, "ADMIN: Kicking %s." % player_name], skt)

            else:
                _pb_kick(player_name)
        else:
            playeryell(admin, ['!playeryell', admin, "ADMIN: Can't kick admins."], skt)

def kicksay(admin, words, skt):
    global command_q

    if admin not in admins:
        return

    _players = get_players_names(skt)

    player_name = select_player(words[1], _players, admin, skt)

    if player_name == 1:
        return
    elif player_name == 2:
        return
    else:
        if player_name not in admins or player_name == 'Therms':
            punkb_getter = action_pool.spawn(_get_var, 'vars.punkBuster', skt)
            punkb = punkb_getter.wait()
            if _get_var('vars.punkBuster', skt) == 'false':
                playeryell(admin, ['!playeryell', admin, 'ADMIN: !kicksay is only available when Punkbuster is enabled'], skt)
                return
            else:
                _pb_kick(player_name, time=1, reason = ' '.join(words[2:]))
        else:
            playeryell(admin, ['!playeryell', admin, "ADMIN: Can't kick admins."], skt)

def ban(admin, words, skt):
    global command_q

    if admin not in admins:
        return

    try:
        duration = int(words[1])
    except:
        duration = 0

    _players = get_players_names(skt)

    if duration:
        player_name = select_player(words[2], _players, admin, skt)
    else:
        player_name = select_player(words[1], _players, admin, skt)

    if player_name == 1:
        return
    elif player_name == 2:
        return
    else:
        if player_name not in admins:
            punkb_getter = action_pool.spawn(_get_var, 'vars.punkBuster', skt)
            punkb = punkb_getter.wait()
            if punkb == 'false':
                if duration:
                    cmd = 'admin.banPlayer %s seconds %i' % (player_name, duration)
                    command_qer(cmd)
                else:
                    cmd = 'admin.banPlayer %s perm'
                    command_qer(cmd)
            else:
                if duration:
                    d = duration/60
                    if d < 1:
                        d = 1
                    _pb_kick(player_name, d)
                else:
                    cmd = _pb_cmd('PB_SV_Ban %s' % player_name)
                    command_qer(cmd)
        else:
            playeryell(admin, ['!playeryell', admin, "ADMIN: Can't kick admins."], skt)

def unban(admin, words, skt):
    global command_q

    if admin not in admins:
        return

    try:
        duration = int(words[1])
    except:
        duration = 0

    cmd = "admin.unbanPlayer " % words[1]
    print "COMMAND: %s" % cmd
    command_qer(cmd)

    cmd = ""
    cmd = _pb_cmd()
    ################WORKING HERE

############################################################
#Command helpers
############################################################
def countdown(msg, seconds, player, skt):
    playeryell(sys_admin, ["!playeryell", 5, "Therms", "BEGINNING COUNTDOWN"], skt)
    time.sleep(5)
    for i in reversed(range(seconds)):
        msg_mod = "%s (%i seconds)" % (msg, i)
        words = ["!playeryell", 1, player, msg_mod]
        playeryell(sys_admin, words, skt)
        time.sleep(1)

def _pb_kick(player, time = 1, reason=False):
    cmd_text = 'PB_SV_Kick "%s" %i' % (player, time)

    if reason:
        cmd_text += " %s" % reason

    cmd = _pb_cmd(cmd_text)
    command_qer(cmd)

def get_map(skt):
    level = send_command_n_return("admin.currentLevel", skt)[1].split("/")[1].lower()
    return maps[level]

def get_players(skt):
    ''' '''
    #sample=['OK', '[CIA]', 'Therms', '24', '1', '', 'cer566', '24', '2']
    cmd = "admin.listPlayers all"

    players = send_command_n_return(cmd, skt=skt)

    if players[0] == 'OK':
        players = players[1:]

    field_count = 0
    players_output = []
    p =[]
    for player in players:
        p.append(player)
        field_count += 1
        if field_count == 4:
                field_count = 0
                players_output.append(tuple(p))
                p = []

    return players_output

def get_clans(skt):
    ''' Returns a dict of clan: players.
    '''
    players = get_players(skt)

    #filter the list
    field_count = 0
    players_l = []
    clans = {}
    for player in players:
        if player[0] in clans:
            clans[player[0]].append(player[1])
        else:
            clans[player[0]] = [player[1]]

    return clans

def get_players_names(skt):
    ''' Returns a list of player names on the server.
    '''
    _players = get_players(skt)
    players = [x[1] for x in _players]

    return players

def select_player(player, players, admin, skt):
    ''' Selects a player from a list of players.  Can use player name substrings.
        If substring isn't unique enough, or if no matches are found, we'll
        message the admin who initiated the command informing them of this.
    '''
    #Find player amongst players

    #print "searching for %s amonst %s at %s auth" % (player, players, admin)
    matches = []
    for p in players:
        if player.lower() in p.lower():
            matches.append(p)

    if len(matches) > 1:
        #Not specific enough
        playeryell(sys_admin, ['!playeryell', admin, 'ADMIN: Be more specific with playername.'], skt)
        return 1
    elif len(matches) == 0:
        #No matches
        playeryell(sys_admin, ['!playeryell', admin, 'ADMIN: No matching playername.'], skt)
        return 2
    else:
        return matches[0]
############################################################
#Connection
############################################################
def _server_connect(host, port):
    ''' Connects to a server and returns the socket
    '''
    #open socket
    server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server_socket.connect((host, port))
    return server_socket

def _auth(skt, pw):
    ''' Authenticates to server on socket
    '''
    # Retrieve this connection's 'salt'
    words = _send_command(skt, "login.hashed")

    # Given the salt and the password, combine them and compute hash value
    salt = words[1].decode("hex")
    pw_hash = bc2_misc._hash_pw(salt, pw)
    pw_hash_encoded = pw_hash.encode("hex").upper()

    # Send password hash to server
    words = _send_command(skt, "login.hashed " + pw_hash_encoded)

    # if the server didn't like our password, abort
    if words[0] != "OK":
        raise ValueError("Incorrect password")

    return skt

def _send_command(skt, cmd):
    ''' Send cmd over skt.
    '''
    global client_seq_number #maintain seqence numbers

    words = shlex.split(cmd)
    request, client_seq_number = bc2_misc._encode_req(words, client_seq_number)

    skt.send(request)

    screen_log("CMD SENT: %s" % cmd, 3)

    # Wait for response from server
    packet = skt.recv(4096)
    _, is_response, _, words = bc2_misc._decode_pkt(packet)

    if not is_response:
        print 'Received an unexpected request packet from server, ignored:'

    return words

def _set_receive_events(skt):
    _send_command(skt, "eventsEnabled true")

def send_command_n_return(cmd, skt=None):
    ''' Sends a command to the command queue and optionally waits for
        a return.  This is a more expensive option than _send_command
        since we open a new socket.
    '''

    if not skt:
        temp_socket = _server_connect(host, port)
        _auth(temp_socket, pw)
        own_socket = True
    else:
        temp_socket = skt
        own_socket = False

    words = _send_command(temp_socket, cmd)

    if own_socket:
        _send_command(temp_socket, "quit")

    return words

############################################################
#Producers
############################################################
def command_qer(cmd, prio=2):
    global command_q
    command_q.put((prio,cmd))
    screen_log("COMMAND QUEUED: %s" % cmd, 2)

def event_logger_qer(event, recv_time):
    ''' event should be a list of words
    '''
    global event_log_q
    event_log_q.put((event, recv_time))
    screen_log("EVENT QUEUED: %s (approx. total queued: %i)" % (words[0], event_log_q.qsize()), 3)

############################################################
#Event handling
############################################################
def is_pb_new_connection(pb):
    if "PunkBuster Server: New Connection (slot" in pb:
        return True
    return False

def is_pb_lost_connection(pb):
    if "PunkBuster Server: Lost Connection (slot" in pb:
        return True
    return False

def parse_pb_new_connection(pb):
    try:
        name = re.search(r'"[-\w&()*+./:;<=>?\[\]\^{|} ]{4,16}"', pb).group()[1:-1]
        ip = re.search(r'\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b', pb).group()
    except:
        return
    return name, ip

def parse_pb_lost_connection(pb):
    try:
        name = re.search(r'\) [-\w&()*+./:;<=>?\[\]\^{|} ]{4,16}\n', pb).group()[2:-1]
        pb_guid = re.search(r'\b\w{30,50}\(', pb).group()[:-1]
    except:
        return
    return name, pb_guid

def event_onchat(words, recv_time):
    global event_chat_q
    player = words[1]
    text = words[2]
    stmt_insert = "INSERT INTO bc2_chat (player, text) VALUES (%s, %s)"

def find_blank_playername(cursor):
    ''' Checks each player currently on the server against our log of player
        connections.  If the player isn't in our log, it means he is the one
        who BC2's player.onJoin event reported a blank playername for.
    '''
    global command_socket
    screen_log("Attempting to find blank player name.", 2)
    players = get_players_names(command_socket)
    num_tries = 3
    for i in range(num_tries):
        for player in players:
            stmt_select = "SELECT * FROM bc2_connections WHERE player = '%s' AND leavetime IS NULL" % player
            try:
                cursor.execute(stmt_select)
            except:
                import pdb; pdb.set_trace()
            results = cursor.fetchall()
            if len(results) == 0:
                screen_log("Found blank playername %s" % player, 2)
                if player != None:
                    if player != "None":
                        return player
            screen_log("%s isn't the blank playername" % player, 3)

        time.sleep(1)


############################################################
#Misc
############################################################
def server_manager():
    global mix_conquest_rush
    our_skt = _server_connect(host, port)
    _auth(our_skt, pw)
    screen_log("Server manager thread started", 2)
    map = send_command_n_return("admin.currentLevel", skt=our_skt)
    gamemode = send_command_n_return("admin.getPlaylist", skt=our_skt)
    gamemodes = ['rush', 'conquest']
    while True:
        curr_map = get_map(our_skt)
        curr_map_gamemode = re.search(r"\(\w+\)", curr_map).group()[1:-1].lower()
        curr_gamemode_setting = send_command_n_return("admin.getPlaylist", our_skt)[1].lower()

        if mix_conquest_rush:
            screen_log("Checking if gamemode needs switched (map: %s, map mode: %s, mode setting: %s)" % (curr_map, curr_map_gamemode, curr_gamemode_setting), 3)
            #if our current gamemode is the same as the gamemode of the map we're playing, change it
            if curr_map_gamemode == curr_gamemode_setting:
                gamemode_options = list(gamemodes)

                #remove our current gamemode setting from our options
                gamemode_options.pop(gamemode_options.index(curr_gamemode_setting))

                #set gamemode to a random choice from our gamemode options
                new_gamemode = random.choice(gamemode_options)
                screen_log("Changing gamemode type to %s" % new_gamemode, 2)
                ret = send_command_n_return("admin.setPlaylist %s" % new_gamemode, skt=our_skt)
                if ret[0] != 'OK':
                    screen_log("ERROR SETTING GAMEMODE", 1)
        time.sleep(10)

def _get_var(var, skt):
    var = send_command_n_return(var, skt=skt)
    return var

def screen_log(msg, level=3):
    ''' Prints messages to the console.
        Each message has an associated level.  If the level is less than or
        equal to output_level it gets displayed.
            Levels:
                0:  Critical
                1:  Error
                2:  Info
                3:  High Volume messages
    '''
    #level 0 = critical

    #least important = level 3
    if level <= output_level:
        dt = datetime.datetime.today().strftime("%m/%d/%y %H:%M:%S")
        print "%s - %s" % (dt, msg)

def _pb_cmd(cmd):
    return 'punkBuster.pb_sv_command "%s"' % cmd

def _get_mysql_config():
    config = open('config\\mysql', 'r').read().split("\n")
    cfg = {}
    for c in config:
        split = shlex.split(c)
        cfg[split[0][:-1]] = split[1]
    return cfg

def create_tables():
    tables = []
    tables.append("""
                  CREATE TABLE IF NOT EXISTS bc2_chat (
                  id INT UNSIGNED NOT NULL AUTO_INCREMENT,
                  dt DATETIME,
                  player VARCHAR(50) DEFAULT '' NOT NULL,
                  chat TEXT,
                  PRIMARY KEY (id))
                  """)
    tables.append("""
                  CREATE TABLE IF NOT EXISTS bc2_connections (
                  id INT UNSIGNED NOT NULL AUTO_INCREMENT,
                  player VARCHAR(50),
                  ip VARCHAR(15),
                  pb_guid VARCHAR(50),
                  jointime DATETIME,
                  leavetime DATETIME,
                  PRIMARY KEY (id))
                  """)
    tables.append("""
                  CREATE TABLE IF NOT EXISTS bc2_punkbuster(
                  id INT UNSIGNED NOT NULL AUTO_INCREMENT,
                  dt DATETIME,
                  punkbuster TEXT,
                  PRIMARY KEY(id))
                  """)
    tables.append("""
                  CREATE TABLE IF NOT EXISTS bc2_kills(
                  id INT UNSIGNED NOT NULL AUTO_INCREMENT,
                  dt DATETIME,
                  victim VARCHAR(50) DEFAULT '' NOT NULL,
                  killer VARCHAR(50) DEFAULT '' NOT NULL,
                  PRIMARY KEY(id))
                  """)
    tables.append("""
                  CREATE TABLE IF NOT EXISTS bc2_admin(
                  id INT UNSIGNED NOT NULL AUTO_INCREMENT,
                  admin VARCHAR(50) DEFAULT '' NOT NULL,
                  command VARCHAR(250) DEFAULT '' NOT NULL,
                  PRIMARY KEY(id))
                  """)
    tables.append("""
                  CREATE TABLE IF NOT EXISTS bc2_btpslog(
                  id INT UNSIGNED NOT NULL AUTO_INCREMENT,
                  dt DATETIME,
                  message TEXT,
                  PRIMARY KEY(id))
                  """)
    cfg = _get_mysql_config()
    db = mysql.connector.Connect(host=cfg['host'],
                             user=cfg['user'],
                             password=cfg['password'],
                             database=cfg['database'])
    cursor = db.cursor()

    for table in tables:
        cursor.execute(table)

    #clean up connection info
    stmt_delete = "DELETE FROM bc2_connections WHERE leavetime IS NULL"
    cursor.execute(stmt_delete)

    db.commit()
    db.close()


client_seq_number = 0

if __name__ == '__main__':
    #config
    host = "75.102.38.3"
    port = 48888
    pw = open("config/password").read().strip()
    admins = open("config/admins").read().split("\n")
    sys_admin = uuid.uuid4()
    admins.append(sys_admin)
    output_level = 3
    maps = {"mp_001": "Panama Canal (Conquest)",
            "mp_003": "Laguna Alta (Conquest)",
            "mp_005": "Atacama Desert (Conquest)",
            "mp_007": "White Pass (Conquest)",
            "mp_009cq": "Laguna Presa (Conquest)",
            "mp_002": "Valparaiso (Rush)",
            "mp_004": "Isla Inocentes (Rush)",
            "mp_006": "Arica Harbor (Rush)",
            "mp_008": "Nelson Bay (Rush)",
            "mp_012gr": "Port Valdez (Squad Rush)",
            "mp_001sr": "Panama Canal (Squad Rush)",
            "mp_002sr": "Valparaiso (Squad Rush)",
            "mp_005sr": "Atacama Desert (Squad Rush)",
            "mp_012sr": "Port Valdez (Squad Rush)",
            "mp_004sdm": "Isla Inocentes (Squad Deathmatch)",
            "mp_006sdm": "Arica Harbor (Squad Deathmatch)",
            "mp_007sdm": "White Pass (Squad Deathmatch)",
            "mp_009sdm": "Laguna Presa (Squad Deathmatch)"}
    mix_conquest_rush = True

    #pool of green threads for action
    action_pool = eventlet.GreenPool()

    #queue of commands to send to BC2 server
    command_q = eventlet.queue.PriorityQueue()

    #event logging queue
    event_log_q = eventlet.Queue()

    #command return queue
    log_q = eventlet.Queue()

    #dictionary of commands with their functions
    cmds = {"!serveryell": serveryell,
            "!playeryell": playeryell,
            "!map": map_,
            "!kick": kick,
            "!kicksay": kicksay,
            "!ban": ban,
            "!gonext": gonext}

    #establish connection for event stream
    event_socket = _server_connect(host, port)
    _auth(event_socket, pw)
    _set_receive_events(event_socket)

    #establish connection for outgoing commands
    command_socket = _server_connect(host, port)
    _auth(command_socket, pw)

    #spawn counsumer threads
    action_pool.spawn_n(command_processor)
    action_pool.spawn_n(event_logger)
    action_pool.spawn_n(log_processor)
    action_pool.spawn_n(server_manager)

    #countdown("HEYHEY", 10, "Therms", command_socket)
    #action_pool.spawn_n(countdown, "Partytime in", 10, "Therms", command_socket)


    while True:
        #get packet
        packet = event_socket.recv(4096)
        try:
            #decode packet
            _, is_response, sequence, words = bc2_misc._decode_pkt(packet)
            #ack packet
            if not is_response:
                response = bc2_misc._encode_resp(sequence, ["OK"])
                event_socket.send(response)
        except:
            continue

        if len(words) > 0:
            recv_time = datetime.datetime.now()

            #send event to the event processor queue for logging
            event_logger_qer(words, recv_time)

            #process event
            if words[0] == 'player.onChat':
                try:
                    chat_words = shlex.split(words[2])
                except:
                    continue

                talker = words[1]
                potential_cmd = chat_words[0].lower()
                if potential_cmd in cmds:
                    #send command to the appropriate function
                    cmds[potential_cmd](talker, chat_words, command_socket)
            if words[0] == 'player.onJoin':
                pass
            if words[0] == 'player.onLeave':
                pass
            if words[0] == 'player.onKill':
                pass
            if words[0] == 'punkBuster.onMessage':
                pass
        else:
            continue
