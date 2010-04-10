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
import sys
import uuid

from pkg import bc2_misc
from eventlet.green import socket
from eventlet.green import time
import mysql.connector

############################################################
#Consumer loops
############################################################
def thread_manager():
    ''' If a thread is killing itself it will put a message in thread_q.  We
        fetch that message and restart it.
    '''

    global thread_q
    screen_log("Thread manangement thread started", 2)

    thread_msgs = {"event_logger": event_logger,
                   "test_thread": test_thread}

    while True:
        if thread_q.empty():
            continue

        msg = thread_q.get()
        screen_log("%s thread died, restarting..." % msg, 1)
        action_pool.spawn_n(thread_msgs[msg])

def test_thread():
    global thread_q
    screen_log("Test thread started", 1)
    time.sleep(10)
    thread_q.put("test_thread")
    return


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
        if log_q.empty():
            time.sleep(.1)
            continue

        msg = log_q.get()
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
    global command_q
    screen_log("Command processor thread started", 2)

    while True:
        if command_q.empty():
            time.sleep(.1)
            continue


        cmd = command_q.get()
        msg_level = cmd[2]
        cmd = cmd[1]
        _send_command(command_socket, cmd, msg_level)

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
    event_error = False

    while True:
        #log any errors
        if event_error:
            log_q.put(event_error)
            screen_log(event_error, 1)
            event_error = False

        #loop waiting for events
        if event_log_q.empty():
            time.sleep(.1)
            continue

        #fetch event from queue
        evt = event_log_q.get()

        #format time
        recv_time = evt[1].strftime("%Y-%m-%d %H:%M:%S")

        #we only care about [0] from now on
        evt = evt[0]

        if evt[0] == 'player.onChat':
            #log chat
            sql = "INSERT INTO bc2_chat (dt, player, chat) VALUES (%s, %s, %s)"
            try:
                cursor.execute(sql, (recv_time, evt[1], evt[2]))
                db.commit()
                screen_log("EVT_LOGGED: player.onChat - %s: %s" % (evt[1], evt[2]), 3)
            except:
                import pdb; pdb.set_trace()
                event_error = "Error executing SQL: %s" % sql % (recv_time, evt[1], evt[2])


        elif evt[0] == 'player.onJoin':
            #Do things for when player joins server
            sql = "INSERT INTO bc2_connections (player, jointime) VALUES (%s, %s)"
            if evt[1] == '':
                event_error = "Blank player name"
                pass
            else:
                #log new connection
                #screen_log("player.onJoin - %s" % evt[1], 3)
                pass

        elif evt[0] == 'player.onAuthenticated':
            #Since player.onJoin sometimes give empty playernames, we're just
            #going to start using onAuthenticated
            sql = "INSERT INTO bc2_connections (player, jointime, ea_guid) VALUES (%s, %s, %s)"
            try:
                cursor.execute(sql, (evt[1], recv_time, evt[2]))
                db.commit()
                screen_log("EVT_LOGGED: player.onAuthenticated - %s" % evt[1], 3)
            except:
                import pdb; pdb.set_trace()
                event_error = "Error executing SQL: %s" % sql % (evt[2], evt[1])


        elif evt[0] == 'player.onLeave':
            #Do things for when player leaves server
            #log disconnect
            sql = """UPDATE bc2_connections
                                SET leavetime=%s
                                WHERE player=%s
                                AND leavetime is NULL
                                AND %s > jointime"""
            try:
                cursor.execute(sql, (recv_time, evt[1], recv_time))
                db.commit()
                screen_log("EVT_LOGGED: player.onLeave - %s" % evt[1], 3)
            except:
                import pdb; pdb.set_trace()
                event_error = "Error executing SQL: %s" % sql % (recv_time, evt[1], recv_time)


        elif evt[0] == 'player.onKill':
            sql = "INSERT INTO bc2_kills (dt, victim, killer) VALUES (%s, %s, %s)"
            try:
                cursor.execute(sql, (recv_time, evt[2], evt[1]))
                db.commit()
                screen_log("EVT_LOGGED: player.onKill - %s killed %s" % (evt[1], evt[2]), 3)
            except:
                import pdb; pdb.set_trace()
                event_error = "Error executing SQL: %s" % sql % (recv_time, evt[2], evt[1])


        elif evt[0] == 'punkBuster.onMessage':
            #import pdb; pdb.set_trace()
            if is_pb_new_connection(evt[1]):
                try:
                    name, ip = parse_pb_new_connection(evt[1])
                    parsed = True
                except:
                    import pdb; pdb.set_trace()
                    log_q.put("ERROR:  Can't parse punkbuster new connection msg (%s)." % evt[1])
                    screen_log("ERROR: Can't parse %s" % evt[1])
                    parsed = False

                if parsed:
                    sql = "UPDATE bc2_connections SET ip=%s WHERE player=%s AND leavetime IS NULL"
                    try:
                        cursor.execute(sql, (ip, name))
                        db.commit()
                        screen_log(r"EVT_LOGGED: punkBuster.onMessage: %s" % evt[1], 3)
                    except:
                        import pdb; pdb.set_trace()
                        event_error = "Error executing SQL: %s" % sql % (ip, name)
                        print event_error
                        import pdb; pdb.set_trace()

            elif is_pb_lost_connection(evt[1]):
                try:
                    name, pb_guid = parse_pb_lost_connection(evt[1])
                    parsed = True
                except:
                    import pdb; pdb.set_trace()
                    log_q.put("ERROR:  Can't parse punkbuster lost connection msg (%s)." % evt[1])
                    screen_log("ERROR: Can't parse %s" % evt[1])
                    parsed = False

                if parsed:
                    sql = "UPDATE bc2_connections SET pb_guid=%s WHERE player=%s and pb_guid IS NULL"
                    try:
                        cursor.execute(sql, (pb_guid, name))
                        db.commit()
                        screen_log(r"EVT_LOGGED: punkBuster.onMessage: %s" % evt[1], 3)
                    except:
                        import pdb; pdb.set_trace()
                        event_error = "Error executing SQL: %s" % sql % (pb_guid, name)
                        print event_error
                        import pdb; pdb.set_trace()

            #log punkbuster messages
            sql = "INSERT INTO bc2_punkbuster (dt, punkbuster) VALUES (%s, %s)"
            try:
                cursor.execute(sql, (recv_time, evt[1]))
                db.commit()
                screen_log(r"EVT_LOGGED: punkBuster.onMessage: %s" % evt[1], 3)
            except:
                import pdb; pdb.set_trace()
                event_error = "Error executing SQL: %s" % sql % (recv_time, evt[1])
        else:
            pass

############################################################
#Command functions
############################################################
def serversay(admin, words, skt):
    ''' Command action.
        Words should be a list formatted like:
            ["!serversay", "message"]
    '''
    if admin not in admins:
        return

    if len(words) < 2:
        _playersay(admin, 'ADMIN: Must specify text to say.')
        return

    msg = ' '.join(words[1:])

    _serversay(msg)

def playersay(admin, words, skt):
    ''' Command action.
        Words should be a list formatted like:
            ["!playeryell", "playername", "message"]
    '''
    global command_q

    if admin not in admins:
        return

    player = words[1]
    msg = ' '.join(words[2:])

    player_name = select_player(player, get_players_names(skt), admin, skt)

    if player_name == 1:
        return
    elif player_name == 2:
        return
    else:
        _playersay(player_name, msg)

def serveryell(admin, words, skt):
    ''' Command action.
        Words should be a list formatted like:
            ["!serveryell", "message", "words", "are", "split", "or", "not"]

        If duration isn't included we default to 4 seconds.
    '''
    if admin not in admins:
        return

    default_duration = 4

    print _get_variable(shlex.split("text var=whatup more text"), 'var')

    if len(words) < 1:
        _playersay(admin, 'ADMIN: Must specify text to yell.')
        return

    parsed = _get_variable(words, 'd')

    if parsed:
        msg = ' '.join(parsed[0][1:])
        seconds = int(parsed[1])
    else:
        msg = ' '.join(words[1:])
        seconds = default_duration

    _serveryell(msg, seconds)


def playeryell(admin, words, skt):
    ''' Command action.
        Words should be a list formatted like:
            duration included:
                ["!playeryell", "playername", "d=seconds message"]
            duration not included:
                ["!playeryell", "playername", "message"]

        If duration isn't included we default to 4 seconds.
    '''
    global command_q

    if admin not in admins:
        return

    player = words[1]

    default_duration = 4

    if len(words) < 1:
        _playersay(admin, 'ADMIN: Must specify text to yell.')
        return

    parsed = _get_variable(words, 'd')

    if parsed:
        msg = ' '.join(parsed[0][2:])
        seconds = int(parsed[1])
    else:
        msg = ' '.join(words[2:])
        seconds = default_duration

    player_name = select_player(player, get_players_names(skt), admin, skt)

    if player_name == 1:
        return
    elif player_name == 2:
        return
    else:
        try:
            _playeryell(player_name, msg, seconds)
        except:
            _playersay(admin, "Msg failed")
            return
        _playersay(admin, "Msg sent")



def gonext(admin, words, skt):
    ''' Command action.
        Words should be a list formatted like:
            ["!gonext"]
    '''
    if admin not in admins:
        return

    cmd = 'admin.runNextLevel'

    command_qer(cmd, 2, prio=1)

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

    cmd = 'admin.say "%s" player %s' % (level, player)
    command_qer(cmd, 2)

def kick(admin, words, skt):
    if admin not in admins:
        return

    _players = get_players_names(skt)

    player_name = select_player(words[1], _players, admin, skt)

    msg = ' '.join(words[2:])

    if player_name == 1:
        return
    elif player_name == 2:
        return
    else:
        #if player_name not in admins:
        _kick(player_name, msg = msg)
        _playersay(admin, "ADMIN: Kicking %s." % player_name)
        #else:
        #    _playersay(admin, "ADMIN: Can't kick admins.")

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
                playersay(admin, ['!playeryell', admin, 'ADMIN: !kicksay is only available when Punkbuster is enabled'], skt)
                #playeryell(admin, ['!playeryell', admin, 'ADMIN: !kicksay is only available when Punkbuster is enabled'], skt)
                return
            else:
                _pb_kick(player_name, time=1, reason = ' '.join(words[2:]))
        else:
            playersay(admin, ['!playeryell', admin, "ADMIN: Can't kick admins."], skt)
            #playeryell(admin, ['!playeryell', admin, "ADMIN: Can't kick admins."], skt)

def ban(admin, words, skt):
    if admin not in admins:
        return

    _players = get_players_names(skt)

    player_name = select_player(words[1], _players, admin, skt)

    parsed = _get_variable(words, 'd')

    if parsed:
        msg = ' '.join(parsed[0][2:])
        duration = int(parsed[1])
    else:
        msg = ' '.join(words[2:])
        duration = 'perm'

    if player_name == 1:
        return
    elif player_name == 2:
        return
    else:
        if player_name != 'cock':#in admins:
            _ban(player_name, msg=msg, duration=duration)
            #if duration != 'perm':
            #    cmd = 'admin.banPlayer %s seconds %i' % (player_name, duration)
            #    command_qer(cmd, 2)
            #else:
            #    cmd = 'admin.banPlayer %s perm'
            #    command_qer(cmd, 2)
        else:
            _playersay(admin, "ADMIN: Can't ban admins.")

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
    command_qer(cmd, 2)

    cmd = ""
    cmd = _pb_cmd()
    ################WORKING HERE

############################################################
#Command helpers
############################################################
def _get_variable(msg_words, variable):
    ''' Checks list of words for variable=x and returns (msg with variable=x stripped, x)
        or False.
    '''
    for i in range(len(msg_words)):
        found = False
        if msg_words[i][:len(variable)+1] == "%s=" % variable:
            try:
                value = msg_words[i][len(variable)+1:]
                found = True
                break
            except:
                pass

    if not found:
        return False

    msg_words.pop(i)

    return msg_words, value

def _playeryell(player, msg, seconds):
    cmd = 'admin.yell "%s" %s player %s' % (msg, seconds*1000, player)
    command_qer(cmd, 2, prio=1)

def _serveryell(msg, seconds):
    #Build command.  Duration is in ms
    cmd = 'admin.yell "%s" %s all' % (msg, seconds*1000)

    #insert command into command queue
    command_qer(cmd, 2, prio=1)

def _kick(player, msg = None):
    cmd = "admin.kickPlayer %s " % player
    if msg:
        cmd = '%s "%s"' % (cmd, msg)

    _serversay("Kicking: %s" % player)
    command_qer(cmd, 2, prio=0)


def _ban(player, msg = None, duration = "perm"):
    cmd = "banList.add name %s" % player
    if duration == "perm":
        cmd = "%s perm" % cmd
        server_msg = "Permaban: %s" % player
    else:
        cmd = "%s seconds %s" % (cmd, duration)
        server_msg = "Tempban: %s" % player
    if msg:
        cmd = '%s "%s"' % (cmd, msg)

    _serversay(server_msg)
    command_qer(cmd, 2, prio=0)


def _serversay(msg):
    cmd = 'admin.say "%s", all' % msg
    command_qer(cmd, 2, prio = 1)

def _playersay(player, msg):
    cmd = 'admin.say "%s" player %s' % (msg, player)
    command_qer(cmd, 2, prio=1)

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
    command_qer(cmd, 2)

def get_map(skt, msg_level=2):
    level = get_level(skt, msg_level=msg_level).split("/")[1].lower()

    return maps.get(level, level)

def get_level(skt, msg_level=2):
    level = send_command_n_return("admin.currentLevel", msg_level, skt=skt)[1]
    return level

def get_players(skt):
    ''' '''
    #R8sample=['OK', '[CIA]', 'Therms', '24', '1', '', 'cer566', '24', '2']
    ''' R9sample=['OK', '9', 'clanTag', 'name', 'guid', 'teamId', 'squadId', 'kills', 'deaths', '
        score', 'ping', '1', '[CIA]', 'Therms', 'EA_90A459A210EE309C3BD522C0B7F8A276', '
        1', '0', '0', '0', '0', '0']
    '''
    cmd = "admin.listPlayers all"

    players = send_command_n_return(cmd, 2, skt=skt)

    if players[0] == 'OK' and int(players[11]) > 0:
        fields = int(players[1])
        players = players[12:]
    else:
        raise ValueError("Couldn't get players")

    field_count = 0
    players_output = []
    p =[]
    for player in players:
        p.append(player)
        field_count += 1
        if field_count == fields:
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
        playersay(sys_admin, ['!playeryell', admin, 'ADMIN: Be more specific with playername.'], skt)
        playeryell(sys_admin, ['!playeryell', admin, 'ADMIN: Be more specific with playername.'], skt)
        return 1
    elif len(matches) == 0:
        #No matches
        playersay(sys_admin, ['!playeryell', admin, 'ADMIN: No matching playername.'], skt)
        #playeryell(sys_admin, ['!playeryell', admin, 'ADMIN: No matching playername.'], skt)
        return 2
    else:
        return matches[0]

############################################################
#Connection
############################################################
def db_is_online():
    screen_log("Database availability thread started", 2)
    cfg = _get_mysql_config()
    db = mysql.connector.Connect(host=cfg['host'],
                             user=cfg['user'],
                             password=cfg['password'],
                             database=cfg['database'])
    while True:
        try:
            cursor = db.cursor()
            cursor.execute("select version()")
            _ = cursor.fetchall()
        except:
            screen_log("LOST DB CONNECTION", 0)
            sys.exit()

        time.sleep(20)

def server_is_online():
    screen_log("Server availability  thread started", 2)
    skt = _server_connect(host, port, timeout=5)
    _auth(skt, pw)
    while True:
        try:
            _ = send_command_n_return("version", 4, skt=skt)
        except:
            screen_log("SERVER CONNECTION LOST", 0)
            log_q.put("SERVER CONNECTION LOST")
            sys.exit()

        time.sleep(20)

def _server_connect(host, port, timeout=None):
    ''' Connects to a server and returns the socket
    '''
    #open socket
    server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    if timeout:
        server_socket.settimeout(5)
    server_socket.connect((host, port))
    return server_socket

def _auth(skt, pw):
    ''' Authenticates to server on socket
    '''
    # Retrieve this connection's 'salt'
    words = _send_command(skt, "login.hashed", 2)

    # Given the salt and the password, combine them and compute hash value
    salt = words[1].decode("hex")
    pw_hash = bc2_misc._hash_pw(salt, pw)
    pw_hash_encoded = pw_hash.encode("hex").upper()

    # Send password hash to server
    words = _send_command(skt, "login.hashed " + pw_hash_encoded, 2)

    # if the server didn't like our password, abort
    if words[0] != "OK":
        raise ValueError("Incorrect password")

    return skt

def _send_command(skt, cmd, msg_level):
    ''' Send cmd over skt.
    '''
    global client_seq_number #maintain seqence numbers

    words = shlex.split(cmd)
    request, client_seq_number = bc2_misc._encode_req(words, client_seq_number)

    skt.send(request)

    screen_log("CMD SENT: %s" % cmd, msg_level)

    # Wait for response from server
    recv_buffer = ''
    [packet, recv_buffer] = bc2_misc.recv_pkt(skt, recv_buffer)
    #packet = skt.recv(4096)
    _, is_response, _, words = bc2_misc._decode_pkt(packet)

    if not is_response:
        print 'Received an unexpected request packet from server, ignored:'

    return words

def _set_receive_events(skt):
    _send_command(skt, "eventsEnabled true", 2)

def send_command_n_return(cmd, msg_level, skt=None):
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

    words = _send_command(temp_socket, cmd, msg_level)

    if own_socket:
        _send_command(temp_socket, "quit", msg_level)

    return words

############################################################
#Producers
############################################################
def command_qer(cmd, msg_level, prio=2):
    global command_q
    command_q.put((prio, cmd, msg_level))
    screen_log("COMMAND QUEUED: %s" % cmd, 2)

def event_logger_qer(event, recv_time):
    ''' event should be a list of words
    '''
    global event_log_q
    event_log_q.put((event, recv_time))

    qs = event_log_q.qsize()
    if qs > 5:
        msg = "EVENT QUEUE SIZE: %s" % qs
        screen_log(msg, 3)
        log_q.put(msg)

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
                return 0
            results = cursor.fetchall()
            if len(results) == 0:
                screen_log("Found blank playername %s" % player, 2)
                return player
            screen_log("%s isn't the blank playername" % player, 3)

        time.sleep(1)


############################################################
#Misc
############################################################
def get_supported_maps(playlist, skt):
    cmd = "admin.supportedMaps %s" % playlist.upper()
    maps = send_command_n_return(cmd, 2, skt=skt)
    return maps[1:]

def change_playlist(playlist, skt):
    cmd = "admin.setPlaylist %s" % playlist.upper()
    _send_command(skt, cmd, 2)
    return

def append_map(map, skt):
    cmd = 'mapList.append "%s"' % map
    _send_command(skt, cmd, 2)
    return

def clear_maplist(skt):
    cmd = 'mapList.clear'
    _send_command(skt, cmd, 2)
    return

def change_maplist(maps, skt):
    clear_maplist(skt)
    for m in maps:
        append_map(m, skt)

def server_manager():
    global mix_conquest_rush
    our_skt = _server_connect(host, port)
    _auth(our_skt, pw)
    screen_log("Server manager thread started", 2)

    gamemodes = ['RUSH', 'CONQUEST']
    gamemode_maps = dict.fromkeys(gamemodes)

    #get maps supported for each gamemode
    for gm in gamemode_maps:
        gamemode_maps[gm] = {}
        gamemode_maps[gm]['maps'] = []
        gamemode_maps[gm]['last_played'] = 0
        supported_maps = get_supported_maps(gm, our_skt)
        random.shuffle(supported_maps)

        gamemode_maps[gm]['maps'] = supported_maps

    while True:
        try:
            curr_map = get_level(our_skt, msg_level=4)
            for gm in gamemode_maps:
                if curr_map in gamemode_maps[gm]['maps']:
                    curr_map_gamemode = gm
            curr_gamemode_setting = send_command_n_return("admin.getPlaylist", 4, our_skt)[1]

            if mix_conquest_rush:
                screen_log("Checking if gamemode needs switched (map: %s, map mode: %s, mode setting: %s)" % (curr_map, curr_map_gamemode, curr_gamemode_setting), 4)
                #if our current gamemode is the same as the gamemode of the map we're playing, change it
                if curr_map_gamemode.lower() == curr_gamemode_setting.lower():
                    gamemode_options = list(gamemodes)

                    #remove our current gamemode setting from our options
                    gamemode_options.pop(gamemode_options.index(curr_gamemode_setting))

                    #set gamemode to a random choice from our gamemode options
                    new_gamemode = random.choice(gamemode_options)
                    screen_log("Changing gamemode type to %s" % new_gamemode, 2)
                    change_playlist(new_gamemode, our_skt)
                    level = send_command_n_return("admin.currentLevel", 2, skt=our_skt)[1]
                    gamemode_maps[curr_map_gamemode.upper()]['last_played'] = gamemode_maps[curr_map_gamemode]['maps'].index(level)

                    #build new maplist
                    new_gamemode_maps = gamemode_maps[new_gamemode]['maps']
                    new_gamemode_lastp = gamemode_maps[new_gamemode]['last_played']
                    new_maplist = new_gamemode_maps[new_gamemode_lastp+1:]
                    new_maplist.extend(new_gamemode_maps[:new_gamemode_lastp+1])

                    #send new maplist to server
                    clear_maplist(our_skt)
                    change_maplist(new_maplist, our_skt)

        except:
            screen_log("error in game mode mixer")
            log_q.put("error in game mode mixer")

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
                4:  Super high volume messages
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
                  ea_guid VARCHAR(50),
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
    host = "68.232.176.204"
    #host="75.102.38.3"
    port = 48888
    pw = open("config/password").read().strip()
    admins = open("config/admins").read().split("\n")
    sys_admin = uuid.uuid4()
    admins.append(sys_admin)
    output_level = 3
    maps = {"mp_001": "Panama Canal (Conquest)",
            "mp_003": "Laguna Alta (Conquest)",
            "mp_005": "Atacama Desert (Conquest)",
            "mp_006cq": "Arica Harbor (Conquest)",
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

    #btps log queue
    log_q = eventlet.Queue()

    #thread management queue
    thread_q = eventlet.Queue()

    #dictionary of commands with their functions
    cmds = {"!serveryell": serveryell,
            "!playeryell": playeryell,
            "!map": map_,
            "!kick": kick,
            "!kicksay": kicksay,
            "!ban": ban,
            "!gonext": gonext,
            "!playersay": playersay,
            "!serversay": serversay}

    #establish connection for event stream
    event_socket = _server_connect(host, port)
    _auth(event_socket, pw)
    _set_receive_events(event_socket)

    #establish connection for outgoing commands
    command_socket = _server_connect(host, port)
    _auth(command_socket, pw)

    #spawn threads
    #action_pool.spawn_n(thread_manager)
    action_pool.spawn_n(command_processor)
    action_pool.spawn_n(event_logger)
    action_pool.spawn_n(log_processor)
    action_pool.spawn_n(server_manager)
    #action_pool.spawn_n(db_is_online)
    #action_pool.spawn_n(server_is_online)

    #countdown("HEYHEY", 10, "Therms", command_socket)
    #action_pool.spawn_n(countdown, "Partytime in", 10, "Therms", command_socket)

    event_msg_prio = 2

    recv_buffer = ''

    while True:
        #get packet
        #packet = event_socket.recv(4096)
        [packet, recv_buffer] = bc2_misc.recv_pkt(event_socket, recv_buffer)
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
            screen_log("EVT_RECV: %s" % words, event_msg_prio)

            #send event to the event processor queue for logging
            event_logger_qer(words, recv_time)

            #process event
            if words[0] == 'player.onChat':
                try:
                    chat_words = re.sub("'", "", words[2])
                    chat_words = re.sub('"', "", chat_words)
                    chat_words = shlex.split(chat_words)
                except:
                    continue

                talker = words[1]
                potential_cmd = chat_words[0].lower()

                if potential_cmd[0] == "/":
                    #bc2 hides chat text that starts with a "/" so
                    #we'll use that as an alternate command prefix
                    potential_cmd = "!" + potential_cmd[1:]

                if potential_cmd in cmds:
                    #send command to the appropriate function
                    cmds[potential_cmd](talker, chat_words, command_socket)
