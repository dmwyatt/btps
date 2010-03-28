#first we have to monkey-patch standard library to support green threads
import eventlet
eventlet.monkey_patch()

import datetime
import hashlib
import os
import re
import shlex
from struct import *
import uuid

from pkg import bc2_misc
from eventlet.green import socket
from eventlet.green import time
import mysql.connector
#import socket

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
    words = send_command(skt, "login.hashed")
    #salt_req, client_seq_number = bc2_misc._encode_req(["login.hashed"], client_seq_number)
    #skt.send(salt_req)
    #salt_req_response = skt.recv(4096)
    ##[isFromServer, isResponse, sequence, words] = DecodePacket(getPasswordSaltResponse)
    #is_from_server, is_response, sequence, words = bc2_misc._decode_pkt(salt_req_response)

    # Given the salt and the password, combine them and compute hash value
    salt = words[1].decode("hex")
    pw_hash = bc2_misc._hash_pw(salt, pw)
    pw_hash_encoded = pw_hash.encode("hex").upper()

    # Send password hash to server
    words = send_command(skt, "login.hashed " + pw_hash_encoded)
    #login_req, client_seq_number = bc2_misc._encode_req(["login.hashed", pw_hash_encoded], client_seq_number)
    #skt.send(login_req)
    #login_resp = skt.recv(4096)
    ##[isFromServer, isResponse, sequence, words] = self._decode_pkt(login_resp)
    #_, _, _, words = bc2_misc._decode_pkt(login_resp)

    # if the server didn't like our password, abort
    if words[0] != "OK":
        raise ValueError("Incorrect password")

    return skt

def send_command(skt, cmd):
    ''' Send cmd over skt.
    '''
    global client_seq_number #maintain seqence numbers

    words = shlex.split(cmd)
    request, client_seq_number = bc2_misc._encode_req(words, client_seq_number)

    skt.send(request)

    screen_log("CMD SENT: %s" % cmd)

    # Wait for response from server
    packet = skt.recv(4096)
    _, is_response, _, words = bc2_misc._decode_pkt(packet)

    if not is_response:
        print 'Received an unexpected request packet from server, ignored:'

    return words

def _set_receive_events(skt):
    send_command(skt, "eventsEnabled true")

def gonext(admin, words, skt):
    if admin not in admins:
        return

    cmd = 'admin.runNextLevel'

    command_qer(cmd)

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
    command_qer(cmd)

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
        command_qer(cmd)

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
            punkb_getter = action_pool.spawn(_get_var, 'vars.punkBuster', skt)
            punkb = punkb_getter.wait()
            print "********************: " + punkb
            if _get_var('vars.punkBuster', skt) == 'false':
                cmd = 'admin.kickPlayer %s' % player_name
                command_qer(cmd)
            else:
                _pb_kick(player_name)
        else:
            playeryell(admin, ['!playeryell', admin, "ADMIN: Can't kick admins."], skt)

def _pb_kick(player, time = 1, reason=False):
    cmd_text = 'PB_SV_Kick "%s" %i' % (player, time)

    if reason:
        cmd_text += " %s" % reason

    cmd = _pb_cmd(cmd_text)
    command_qer(cmd)

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

def _get_var(var, skt):
    var_getter = action_pool.spawn(send_command, skt, var)
    var = var_getter.wait()[1]

    return var

def get_map(skt):
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
    cmd = "admin.currentLevel"

    level_getter = action_pool.spawn(send_command, skt, cmd)

    level = level_getter.wait()[1].split('/')[1].lower()

    for m in maps:
        if m.lower() in level.lower():
            return maps[level] + " (%s)" % m

    return level

def get_players(skt):
    ''' '''
    #sample=['OK', '[CIA]', 'Therms', '24', '1', '', 'cer566', '24', '2']
    cmd = "admin.listPlayers all"

    #spawn a green thread to retrieve the player list
    players_getter = action_pool.spawn(send_command, skt, cmd)

    #get the results from the thread
    players = players_getter.wait()

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
        playeryell(admin, ['!playeryell', admin, 'ADMIN: Be more specific with playername.'], skt)
        return 1
    elif len(matches) == 0:
        #No matches
        playeryell(admin, ['!playeryell', admin, 'ADMIN: No matching playername.'], skt)
        return 2
    else:
        return matches[0]

def command_processor():
    ''' Loops waiting for commands to be in command_q and then sends them to
        the server.
    '''
    global command_socket
    screen_log("Command processor thread started")
    screen_log("Starting command processor loop")
    while True:
        try:
            cmd = command_q.get()
        except:
            continue
        screen_log("FETCHED FROM COMMAND QUEUE: %s" % cmd)
        send_command(command_socket, cmd)

def screen_log(msg):
    dt = datetime.datetime.today().strftime("%m/%d/%y %H:%M:%S")
    print "%s - %s" % (dt, msg)

def _pb_cmd(cmd):
    return 'punkBuster.pb_sv_command "%s"' % cmd

def command_qer(cmd):
    global command_q
    command_q.put(cmd)
    screen_log("COMMAND QUEUED: %s" % cmd)

def event_logger_qer(event, recv_time):
    ''' Implement mysql event logger
    '''
    global event_log_q
    event_log_q.put((event, recv_time))
    screen_log("EVENT QUEUED: %s" % words[0])

def _get_mysql_config():
    config = open('config\\mysql', 'r').read().split("\n")
    cfg = {}
    for c in config:
        split = shlex.split(c)
        cfg[split[0][:-1]] = split[1]
    return cfg

def event_logger():
    ''' Loops waiting for events to be in event_log_q and then logs them to
        mysql.
    '''
    global event_log_q
    global players_q
    screen_log("Event logger thread started")

    #Make sure our tables exist
    create_tables()

    cfg = _get_mysql_config()
    db = mysql.connector.Connect(host=cfg['host'],
                             user=cfg['user'],
                             password=cfg['password'],
                             database=cfg['database'])
    cursor = db.cursor()

    screen_log("Starting event_logger loop")
    while True:
        #loop waiting for events
        try:
            evt = event_log_q.get()
        except:
            continue
        recv_time = evt[1].strftime("%Y-%m-%d %H:%M:%S")

        #we only care about [0] from now on
        evt = evt[0]

        screen_log("FETCHED FROM EVENT QUEUE: %s" % evt)

        if evt[0] == 'player.onChat':
            #log chat
            stmt_insert = "INSERT INTO bc2_chat (dt, player, chat) VALUES (%s, %s, %s)"
            cursor.execute(stmt_insert, (recv_time, evt[1], evt[2]))
            db.commit()


        elif evt[0] == 'player.onJoin':
            #Do things for when player joins server
            if evt[1] == '':
                #sometimes this event returns '', so use alt-method of guessing players name
                evt[1] == get_new_player()

            #log new connection
            stmt_insert = "INSERT INTO bc2_connections (player, jointime) VALUES (%s, %s)"
            cursor.execute(stmt_insert, (evt[1], recv_time))
            db.commit()

            #update our running-balance list
            active_players_add(evt[1])


        elif evt[0] == 'player.onLeave':
            #Do things for when player leaves server

            #log disconnect
            stmt_update = """UPDATE bc2_connections
                                SET leavetime=%s
                                WHERE player=%s
                                AND leavetime is NULL
                                AND %s > jointime"""
            cursor.execute(stmt_update, (recv_time, evt[1], recv_time))
            db.commit()


        elif evt[0] == 'player.onKill':
            stmt_insert = "INSERT INTO bc2_kills (dt, victim, killer) VALUES (%s, %s, %s)"
            cursor.execute(stmt_insert, (recv_time, evt[2], evt[1]))
            db.commit()


        elif evt[0] == 'punkBuster.onMessage':
            if is_pb_new_connection(evt[1]):
                try:
                    name, ip = parse_pb_new_connection(evt[1])
                    stmt_update = "UPDATE bc2_connections SET ip=%s WHERE player=%s AND leavetime IS NULL"
                    screen_log("EXECUTING: %s" % stmt_update % (ip, name))
                    cursor.execute(stmt_update, (ip, name))
                    db.commit()
                except:
                    screen_log("ERROR:  Can't parse punkbuster lost connection msg.")

            elif is_pb_lost_connection(evt[1]):
                try:
                    name, pb_guid = parse_pb_lost_connection(evt[1])
                    stmt_update = "UPDATE bc2_connections SET pb_guid=%s WHERE player=%s and pb_guid IS NULL"
                    screen_log("EXECUTING: %s" % stmt_update % (pb_guid, name))
                    cursor.execute(stmt_update, (pb_guid, name))
                    db.commit()
                except:
                    screen_log("ERROR:  Can't parse punkbuster lost connection msg.")
            stmt_insert = "INSERT INTO bc2_punkbuster (dt, punkbuster) VALUES (%s, %s)"
            cursor.execute(stmt_insert, (recv_time, evt[1]))
            db.commit()
        else:
            pass

def get_new_player():
    ''' Compares current player list to our running-balance of players to see
        who is on the server but isn't on our running-balance.
    '''
    global command_socket
    curr_players = get_players_names(command_socket)
    players = get_active_players_list()
    added = 0
    for p in curr_players:
        if p not in players:
            added += 1
    if added == 1:
        return added
    else:
        screen_log("ERROR: Multiple players on server that aren't in active_players")
        return

def active_player_kd(killer, victim):
    global players_q
    while True:
        try:
            players = players_q.get()
            break
        except:
            pass
    players[killer][0] += 1
    players[victim][1] += 1
    players_q.put(players)

def active_players_add(add):
    global players_q
    players = players_q.get()
    players[add] = 0
    players_q.put(players)

def active_players_remove(remove):
    global players_q
    players = players_q.get()
    players[remove].pop()
    players_q.put(players)

def get_active_players_list():
    global players_q
    players = players_q.get()
    players_q.put(players)
    return players

def known_player(player):
    players = players_q.get()
    players_q.put(players)
    if player in players:
        return True
    return False

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

    db.commit()
    db.close()

client_seq_number = 0

if __name__ == '__main__':
    #config
    host = "75.102.38.3"
    port = 48888
    pw = open("config/password").read().strip()
    admins = open("config/admins").read().split("\n")

    #pool of green threads for action
    action_pool = eventlet.GreenPool()

    #queue of commands to send to BC2 server
    command_q = eventlet.Queue()

    #event logging queue
    event_log_q = eventlet.Queue()

    #queue containing updates to our running balance of active players
    players_q = eventlet.Queue()

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

    #get players on server at startup
    players_q.put(dict.fromkeys(get_players_names(command_socket)))

    screen_log("%i players on server." % len(get_active_players_list()))

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

            #send event to the event queue
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
