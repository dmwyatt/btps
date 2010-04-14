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
def server_state():
    '''
        Responds to messages on state_q.
        Messages are as follows:
            ["ST_CHANGE", to team, to squad]
            ["KILL", killer, victim]
            ["AUTH", name, guid]
            ["LEAVE", name]
            ["LEVELLOAD", level]

        We also query the server every ~30 seconds for state information that
        isn't exposed via server events.

        Maintains a global dict of player information.
    '''
    global state_q
    global global_state

    sync_interval = 30
    screen_log("Server state thread started", 2)

    msg_level = 2
    try:
        si = serverinfo(msg_level=2)
        global_state['hostname'] = si['host']
        global_state['maxplayers'] = si['maxplayers']
        global_state['active_playlist'] = si['playlist']
        global_state['playlists'] = si['playlists']
        sync_state()
        atime = time.time()

        while 1:
            if time.time() - atime > 30:
                sync_state()
                atime = time.time()
            if state_q.empty():
                time.sleep(.1)
                continue
            msg = state_q.get()

            if msg[0] == "LEVELLOAD":
                global_state['level'] = msg[1]
                screen_log("STATE UPDATE: level = %s" % global_state['level'], level=msg_level)
                swap_playlists()

            elif msg[0] == "LEAVE":
                try:
                    global_state['players'].pop(msg[1])
                except KeyError:
                    err = "STATE UPDATE: Attempted to remove player who doesn't exist."
                    screen_log(err, level=1)
                    log_q.put(err)
                screen_log("STATE UPDATE: player left: %s" % msg[1], level=msg_level)

            elif msg[0] == "AUTH":
                sync_state()
                screen_log("STATE UPDATE: player joined: %s" % msg[1], level=msg_level)
                #global_state['players'][msg[1]] = dict.fromkeys(global_state['players_info'], 0)
                #global_state['players'][msg[1]]['guid'] = msg[2]

            elif msg[0] == "KILL":
                try:
                    global_state['players'][msg[1]]['kills'] += 1
                    global_state['players'][msg[2]]['deaths'] += 1
                except KeyError:
                    err = "STATE UPDATE: Attempted to update kills/death for nonexistant player"
                    screen_log(err, level=1)
                    log_q.put(err)
                screen_log("STATE UPDATE: %s killed %s" % (msg[1], msg[2]), level=msg_level)

            elif msg[0] == "ST_CHANGE":
                try:
                    global_state['players'][msg[1]]['teamId'] = msg[2]
                    global_state['players'][msg[1]]['squadId'] = msg[3]
                except KeyError:
                    err = "STATE UPDATE: Attempted to change squad/team for nonexistant player"
                    screen_log(err, level=1)
                    log_q.put(err)
                screen_log("STATE UPDATE: %s to team %s, squad %s" % (msg[1], msg[2], msg[3]), level=msg_level)
    except:
        print "SERVER STATE TRACKING FAILURE"
        import pdb; pdb.set_trace()

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
        screen_log("LOGGING to bc2_btpslog: %s" % msg, 2)
        dt = datetime.datetime.today()#.strftime("%m/%d/%y %H:%M:%S")
        stmt_insert = "INSERT INTO bc2_btpslog (dt, message) VALUES (%s, %s)"

        try:
            cursor.execute(stmt_insert, (dt, msg))
            db.commit()
        except:
            screen_log("ERROR LOGGING to bc2_btpslog!!!", 1)

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
        recv_time = evt[1]#.strftime("%Y-%m-%d %H:%M:%S")

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
                event_error = "Error executing SQL: %s" % sql % (recv_time, evt[1], evt[2])

        elif evt[0] == 'server.onLoadingLevel':
            state_q.put(["LEVELLOAD", evt[1]])

        elif evt[0] == 'player.onSquadChange':
            state_q.put(["ST_CHANGE", evt[1], evt[2], evt[3]])

        elif evt[0] == 'player.onTeamChange':
            state_q.put(["ST_CHANGE", evt[1], evt[2], evt[3]])

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
            state_q.put(["AUTH", evt[1], evt[2]])
            sql = "INSERT INTO bc2_connections (player, jointime, ea_guid) VALUES (%s, %s, %s)"
            try:
                cursor.execute(sql, (evt[1], recv_time, evt[2]))
                db.commit()
                screen_log("EVT_LOGGED: player.onAuthenticated - %s" % evt[1], 3)
            except:
                event_error = "Error executing SQL: %s" % sql % (evt[2], evt[1])


        elif evt[0] == 'player.onLeave':
            #Do things for when player leaves server
            #log disconnect
            state_q.put(["LEAVE", evt[1]])
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
                event_error = "Error executing SQL: %s" % sql % (recv_time, evt[1], recv_time)


        elif evt[0] == 'player.onKill':
            state_q.put(["KILL", evt[1], evt[2]])
            sql = "INSERT INTO bc2_kills (dt, victim, killer) VALUES (%s, %s, %s)"
            try:
                cursor.execute(sql, (recv_time, evt[2], evt[1]))
                db.commit()
                screen_log("EVT_LOGGED: player.onKill - %s killed %s" % (evt[1], evt[2]), 3)
            except:
                event_error = "Error executing SQL: %s" % sql % (recv_time, evt[2], evt[1])


        elif evt[0] == 'punkBuster.onMessage':
            if is_pb_new_connection(evt[1]):
                try:
                    name, ip = parse_pb_new_connection(evt[1])
                    parsed = True
                except:
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
                        event_error = "Error executing SQL: %s" % sql % (ip, name)

            elif is_pb_lost_connection(evt[1]):
                try:
                    name, pb_guid = parse_pb_lost_connection(evt[1])
                    parsed = True
                except:
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
                        event_error = "Error executing SQL: %s" % sql % (pb_guid, name)

            #log punkbuster messages
            sql = "INSERT INTO bc2_punkbuster (dt, punkbuster) VALUES (%s, %s)"
            try:
                cursor.execute(sql, (recv_time, evt[1]))
                db.commit()
                screen_log(r"EVT_LOGGED: punkBuster.onMessage: %s" % evt[1], 3)
            except:
                event_error = "Error executing SQL: %s" % sql % (recv_time, evt[1])
        else:
            pass

############################################################
#Command functions
############################################################
def ff(admin, words):
    ''' Command action.
        Words should be a list formatted like:
            ["!ff", "message"]
    '''
    if admin not in admins:
        return

    if len(words) < 2:
        _playersay(admin, "Format: !ff <on|off>")
        return

    if words[1].lower() == 'on':
        resp = friendlyfire_on()
        if resp[0] == 'OK':
            _playersay(admin, "Friendly fire on.")
    elif words[1].lower() == 'off':
        resp = friendlyfire_off()
        if resp[0] == 'OK':
            _playersay(admin, "Friendly fire off.")
    else:
        _playersay(admin, "Format: !ff on/off")

def serversay(admin, words):
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

def playersay(admin, words):
    ''' Command action.
        Words should be a list formatted like:
            ["!playeryell", "playername", "message"]
    '''
    if admin not in admins:
        return

    player = words[1]
    msg = ' '.join(words[2:])

    player_name = select_player(player, get_players_names(), admin)

    if player_name == 1:
        return
    elif player_name == 2:
        return
    else:
        _playersay(player_name, msg)

def serveryell(admin, words):
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


def playeryell(admin, words):
    ''' Command action.
        Words should be a list formatted like:
            duration included:
                ["!playeryell", "playername", "d=seconds message"]
            duration not included:
                ["!playeryell", "playername", "message"]

        If duration isn't included we default to 4 seconds.
    '''
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

    player_name = select_player(player, get_players_names(), admin)

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



def gonext(admin, words):
    ''' Command action.
        Words should be a list formatted like:
            ["!gonext"]
    '''
    if admin not in admins:
        return

    cmd = 'admin.runNextLevel'
    response = send_command(cmd)
    if response[0] != 'OK':
        log_q.put("Error running %s.  Got %s" % (cmd, response))
        _playersay(admin, "Error running next level")

def map_(player, words):
    ''' Command action.
        Words should be a list formatted like:
            duration included:
                ["!playeryell", seconds, "playername", "message"]
            duration not included:
                ["!playeryell", "playername", "message"]

        If duration isn't included we default to 4 seconds.
    '''
    level = get_map()

    _playersay(player, level)

def kick(admin, words):
    if admin not in admins:
        return

    _players = get_players_names()

    player_name = select_player(words[1], _players, admin)

    msg = ' '.join(words[2:])

    if player_name == 1:
        return
    elif player_name == 2:
        return
    else:
        if player_name not in admins:
            _kick(player_name, msg = msg)
            _playersay(admin, "ADMIN: Kicking %s." % player_name)
        else:
            _playersay(admin, "ADMIN: Can't kick admins.")



def ban(admin, words):
    if admin not in admins:
        return

    _players = get_players_names()

    player_name = select_player(words[1], _players, admin)

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
        if player_name not in admins:
            _ban(player_name, msg=msg, duration=duration)
        else:
            _playersay(admin, "ADMIN: Can't ban admins.")


############################################################
#Command helpers
############################################################
def friendlyfire_on(msg_level=2):
    cmd = "vars.friendlyFire true"
    return send_command(cmd, msg_level=msg_level)

def friendlyfire_off(msg_level=2):
    cmd = "vars.friendlyFire false"
    return send_command(cmd, msg_level=msg_level)

def get_friendlyfire(msg_level=2):
    cmd = "vars.friendlyFire"
    return send_command(cmd, msg_level=msg_level)

def serverinfo(msg_level=4):
    ''' This function fetches rarely-changing info.
    '''
    info = send_command("serverInfo", msg_level=msg_level)
    si = {}
    si['host'] = info[1]
    si['currplayers'] = info[2]
    si['maxplayers'] = info[3]
    si['playlist'] = info[4]
    si['level'] = info[5]

    playlists = dict.fromkeys(get_playlists())
    for playlist in playlists:
        playlists[playlist] = {}
        playlists[playlist]['maps'] = get_supported_maps(playlist)
        random.shuffle(playlists[playlist]['maps'])
        playlists[playlist]['last_played'] = 0
        #time.sleep(.5)
    si['playlists'] = playlists
    return si

def sync_state():
    global global_state
    level = get_level(msg_level=4)

    fields, players = parse_players(get_listplayers(msg_level=4))
    global_state['level'] = level
    global_state['players'] = players
    global_state['players_info'] = fields

    for playlist in global_state['playlists']:
        if global_state['level'] in global_state['playlists'][playlist]['maps']:
            global_state['curr_map_playlist'] = playlist

def get_listplayers(msg_level = 2):
    cmd = "admin.listPlayers all"
    return send_command(cmd, msg_level=msg_level)

def _get_variable(msg_words, variable):
    ''' Checks list of words for variable=x and returns (msg with variable=x
        stripped, x) or False if variable is not in msg_words.
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
    response = send_command(cmd)
    if response[0] != 'OK':
        log_q.put("Error running %s.  Got %s" % (cmd, response))
        return False
    return True

def _serveryell(msg, seconds):
    #Build command.  Duration is in ms
    cmd = 'admin.yell "%s" %s all' % (msg, seconds*1000)
    response = send_command(cmd)
    if response[0] != 'OK':
        log_q.put("Error running %s.  Got %s" % (cmd, response))
        return False
    return True

def _kick(player, msg = None):
    cmd = "admin.kickPlayer %s " % player
    if msg:
        cmd = '%s "%s"' % (cmd, msg)

    response = send_command(cmd)
    if response[0] != 'OK':
        log_q.put("Error running %s.  Got %s" % (cmd, response))
        return False

    _serversay("Kicking: %s" % player)
    return True

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

    response = send_command(cmd)
    if response[0] != 'OK':
        log_q.put("Error running %s.  Got %s" % (cmd, response))
        return False

    _serversay(server_msg)
    return True

def _serversay(msg):
    cmd = 'admin.say "%s", all' % msg
    response = send_command(cmd)
    if response[0] != 'OK':
        log_q.put("Error running %s.  Got %s" % (cmd, response))
        return False
    return True


def _playersay(player, msg):
    cmd = 'admin.say "%s" player %s' % (msg, player)

    response = send_command(cmd)

    if response[0] != 'OK':
        log_q.put("Error running %s.  Got %s" % (cmd, response))
        return False
    return True


def countdown(msg, seconds, player):
    playeryell(sys_admin, ["!playeryell", 5, "Therms", "BEGINNING COUNTDOWN"], skt)
    time.sleep(5)
    for i in reversed(range(seconds)):
        msg_mod = "%s (%i seconds)" % (msg, i)
        words = ["!playeryell", 1, player, msg_mod]
        playeryell(sys_admin, words)
        time.sleep(1)

def get_map(msg_level=2):
    #level = get_level(msg_level=msg_level).split("/")[1].lower()

    return global_state['mapnames'].get(global_state['level'], global_state['level'])

def get_level(msg_level=2):
    response = send_command("admin.currentLevel", msg_level=msg_level)

    if response[0] != 'OK':
        log_q.put("Error running %s.  Got %s" % (cmd, response))
        return False


    return response[1]

def parse_players(players):
    ''' '''
    #R8sample=['OK', '[CIA]', 'Therms', '24', '1', '', 'cer566', '24', '2']
    ''' R9sample=['OK', '9', 'clanTag', 'name', 'guid', 'teamId', 'squadId', 'kills', 'deaths', '
        score', 'ping', '1', '[CIA]', 'Therms', '', '1', '0', '0', '0', '0', '0']
    '''
    fieldcount = int(players[1])
    playercount = int(players[fieldcount+2])

    #get fields and their respective positions
    _ = players[2:fieldcount+2]
    fields = {}
    for f in _:
        fields[f] = _.index(f)

    #slice out the player data
    data = players[fieldcount+3:]
    pdata = []

    for i in xrange(fieldcount):
        pdata.append(tuple(data[i::fieldcount]))

    #group each record
    player_records = []
    for p in xrange(playercount):
        player_records.append(tuple([x[p] for x in pdata]))

    #Create dict of records with playername as key
    playerlist = {}
    for i in xrange(playercount):
        _ = {}
        for f in fields:
            try:
                value = int(player_records[i][fields[f]])
            except:
                value = player_records[i][fields[f]]
            if f == 'name':
                name = value
                playerlist[name] = None

            else:
                _[f] = value
        playerlist[name] = _

    return fields.keys(), playerlist

def get_clans():
    ''' Returns a dict of clan: players.
    '''
    players = global_state['players']

    clans = {}
    for name in players:
        clan = playerlist[name]['clanTag']
        if clan not in clans:
            clans[clan] = [name]
        else:
            clans[clan].append(name)

    return clans

def get_players_names():
    ''' Returns a list of player names on the server.
    '''
    return global_state['players'].keys()

def select_player(player, players, admin):
    ''' Selects a player from a list of players.  Can use player name substrings.
        If substring isn't unique enough, or if no matches are found, we'll
        message the admin who initiated the command informing them of this.
    '''
    #Find player amongst players
    matches = []
    for p in players:
        if player.lower() in p.lower():
            matches.append(p)

    if len(matches) > 1:
        #Not specific enough
        _playersay(admin, 'ADMIN: Be more specific with playername.')
        return 1
    elif len(matches) == 0:
        #No matches
        _playersay(admin, 'ADMIN: No matching playername.')
        return 2
    else:
        return matches[0]

############################################################
#Connection
############################################################
def send_command(cmd, msg_level=2):
    global command_socket
    global client_seq_number

    words = shlex.split(cmd)
    request, client_seq_number = bc2_misc._encode_req(words, client_seq_number)

    command_socket.send(request)

    screen_log("CMD SENT: %s" % cmd, msg_level)

    # Wait for response from server
    recv_buffer = ''
    [packet, recv_buffer] = bc2_misc.recv_pkt(command_socket, recv_buffer)
    #packet = command_socket.recv(4096)
    _, is_response, _, words = bc2_misc._decode_pkt(packet)

    if not is_response:
        print 'Received an unexpected request packet from server, ignored:'

    return words

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

############################################################
#Producers
############################################################
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

############################################################
#Misc
############################################################
def get_playlists():
    cmd = "admin.getPlaylists"
    return send_command(cmd)[1:]

def chat_notice(chat_words):
    words = {"ff": ff_notice,
             "friendly fire": ff_notice,
             "ffire": ff_notice,
             "f fire": ff_notice,
             "friendly f": ff_notice}
    chat_words = ' '.join(chat_words)

    for word in words:
        if word in chat_words:
            print "%s is in %s" % (word, chat_words)
            words[word]()
            return

def ff_notice():
    ff = get_friendlyfire(msg_level = 2)
    print ff
    if ff[1].lower() == 'true':
        _serversay("Friendly fire is on")
    else:
        _serversay("Friendly fire is off")

def get_supported_maps(playlist):
    cmd = "admin.supportedMaps %s" % playlist.upper()
    response = send_command(cmd)
    if response[0] != 'OK':
        log_q.put("Error running %s.  Got %s" % (cmd, response))
        screen_log("Error running %s. Got %s" % (cmd, response))

    return response[1:]

def change_playlist(playlist):
    cmd = "admin.setPlaylist %s" % playlist.upper()
    response = send_command(cmd)
    if response[0] != 'OK':
        log_q.put("Error running %s.  Got %s" % (cmd, response))
        screen_log("Error running %s. Got %s" % (cmd, response))
    else:
        global_state['active_playlist'] = playlist

def append_map(map):
    cmd = 'mapList.append "%s"' % map
    response = send_command(cmd)
    if response[0] != 'OK':
        log_q.put("Error running %s.  Got %s" % (cmd, response))
        screen_log("Error running %s. Got %s" % (cmd, response))
        raise ValueError("invalid response (%s)" % response)

def clear_maplist():
    cmd = 'mapList.clear'
    response = send_command(cmd)
    if response[0] != 'OK':
        log_q.put("Error running %s.  Got %s" % (cmd, response))
        screen_log("Error running %s. Got %s" % (cmd, response))
        raise ValueError("invalid response (%s)" % response)

def change_maplist(maps):
    clear_maplist()
    for m in maps:
        append_map(m)

def server_manager():
    global mix_conquest_rush
    screen_log("Server manager thread started", 2)

    gamemodes = ['RUSH', 'CONQUEST']
    gamemode_maps = dict.fromkeys(gamemodes)

    #get maps supported for each gamemode
    for gm in gamemode_maps:
        gamemode_maps[gm] = {}
        gamemode_maps[gm]['maps'] = []
        gamemode_maps[gm]['last_played'] = 0
        supported_maps = get_supported_maps(gm)
        random.shuffle(supported_maps)

        gamemode_maps[gm]['maps'] = supported_maps

    while True:
        try:
            curr_map = get_level(msg_level=4)
            for gm in gamemode_maps:
                if curr_map in gamemode_maps[gm]['maps']:
                    curr_map_gamemode = gm
            curr_gamemode_setting = send_command("admin.getPlaylist", msg_level=4)[1]

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
                    try:
                        change_playlist(new_gamemode)
                    except:
                        raise ValueError("can't change playlist")

                    level = get_level(msg_level=4)
                    gamemode_maps[curr_map_gamemode.upper()]['last_played'] = gamemode_maps[curr_map_gamemode]['maps'].index(level)

                    #build new maplist
                    new_gamemode_maps = gamemode_maps[new_gamemode]['maps']
                    new_gamemode_lastp = gamemode_maps[new_gamemode]['last_played']
                    new_maplist = new_gamemode_maps[new_gamemode_lastp+1:]
                    new_maplist.extend(new_gamemode_maps[:new_gamemode_lastp+1])

                    #send new maplist to server
                    change_maplist(new_maplist)

        except:
            screen_log("error in game mode mixer")
            log_q.put("error in game mode mixer")

        time.sleep(10)

def swap_playlists(gamemodes = ['RUSH', 'CONQUEST']):
    ''' If the active playlist is the same as the playlist of the map we're
        currently playing, pick a new playlist from gamemodes.
    '''

    for playlist in global_state['playlists']:
        if global_state['level'] in  global_state['playlists'][playlist]['maps']:
            global_state['curr_map_playlist'] = playlist
            break
    if global_state['active_playlist'] == global_state['curr_map_playlist']:
        playlist_options = list(gamemodes)

        #remove our current playlist setting from our options
        playlist_options.pop(playlist_options.index(global_state['active_playlist']))

        #set playlist to a random choice from our playlist options
        new_playlist = random.choice(playlist_options)
        screen_log("Changing playlist type to %s" % new_playlist, 2)
        change_playlist(new_playlist)

        #store current map as our last played map for this playlist
        curr_map_playlist = global_state['curr_map_playlist']
        global_state['playlists'][curr_map_playlist]['last_played'] = \
        global_state['playlists'][curr_map_playlist]['maps'].index(global_state['level'])

        new_playlist_maps = global_state['playlists'][new_playlist]['maps']
        new_playlist_lastplayed = global_state['playlists'][new_playlist]['last_played']
        new_maplist = new_playlist_maps[new_playlist_lastplayed+1:]
        new_maplist.extend(new_playlist_maps[:new_playlist_lastplayed+1])

        change_maplist(new_maplist)

def _get_var(var):
    response = send_command(var)
    if response[0] != 'OK':
        log_q.put("Error running %s.  Got %s" % (cmd, response))
        screen_log("Error running %s. Got %s" % (cmd, response))

    return response[1]

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

############################################################
#IRC Bot
############################################################
def irc_colorize(text, color_num):
    ctrl = "\x03"
    t = "%s%s%s%s" % (ctrl, color_num, text, ctrl)
    return t

def irc_q(channel):
    #colors
    ctrl = "\x03"
    red = 4
    greenish = 10

    host = irc_colorize(global_state['hostname'], greenish)
    splitter = irc_colorize("|", red)
    players = irc_colorize("%s/%s" % (len(global_state['players']), global_state['maxplayers']), greenish)
    map = irc_colorize(get_map(), greenish)

    msg = "%s %s %s %s %s" % (host, splitter, players, splitter, map)

    msg = "PRIVMSG %s :%s\r\n" % (channel, msg)

    return msg

def irc_parsemsg(s):
    """Breaks a message from an IRC server into its prefix, command, and arguments.
    """
    prefix = ''
    trailing = []
    if not s:
       raise ValueError("Bad IRC message.")
    if s[0] == ':':
        prefix, s = s[1:].split(' ', 1)
    if s.find(' :') != -1:
        s, trailing = s.split(' :', 1)
        args = s.split()
        args.append(trailing)
    else:
        args = s.split()
    command = args.pop(0)

    for i in xrange(len(args)):
        args[i] = args[i].rstrip()

    return prefix, command, args

def irc_connect(host, port, nick, ident, realname):
    try:
        s = socket.socket()
        s.connect((host, port))
    except:
        raise ValueError("Can't connect to %s:%s" % (host, port))

    s.send("NICK %s\r\n" % nick)
    s.send("USER %s %s bla :%s\r\n" % (ident, host, realname))

    return s

def irc_bot(host, port, nick, ident, realname, channel):

    readbuffer=""
    joined = False

    p_msg = "PRIVMSG %s :%s\r\n"

    s = irc_connect(host, port, nick, ident, realname)

    screen_log("IRC bot thread started")
    while 1:
        try:
            readbuffer=readbuffer+s.recv(1024)
        except:
            screen_log("IRC FAIL: Reconnecting in 60 seconds", level=3)
            time.sleep(60)
            s = irc_connect(host, port, nick, ident, realname)
            continue

        #split up our readbuffer
        temp = readbuffer.split("\n")
        #readbuffer should now contain everthing from last "\n" to the end
        readbuffer=temp.pop( )

        #process each irc message we've received so far
        for line in temp:

            #join our channel...some networks don't let you JOIN immediately
            if not joined:
                hostsplit = host.split(".")
                for component in hostsplit:
                    if component.lower() in line.lower():
                        s.send("JOIN %s\r\n" % channel)
                        joined = True

            prefix, command, args = irc_parsemsg(line)

            if(command=="PING"):
                s.send("PONG %s\r\n" % args[0])
                screen_log('PONGED: %s' % args[0])

            if command == "PRIVMSG":
                try:
                    if args[1] == "!q":
                        s.send(irc_q(args[0]))
                except:
                    import pdb; pdb.set_trace()



client_seq_number = 0
global_state = {}

if __name__ == '__main__':

    ircHOST="irc.us.gamesurge.net"
    ircPORT=6667
    ircNICK="SmackBotTest"
    ircIDENT="sbot"
    ircREALNAME="Thermsbot"
    ircCHANNEL="#clan_cia"

    #config
    host = "68.232.176.204"
    #host="75.102.38.3"
    port = 48888
    pw = open("config/password").read().strip()
    admins = open("config/admins").read().split("\n")
    sys_admin = uuid.uuid4()
    admins.append(sys_admin)
    output_level = 3
    global_state['mapnames'] = {"Levels/MP_001": "Panama Canal (Conquest)",
                                "Levels/MP_003": "Laguna Alta (Conquest)",
                                "Levels/MP_005": "Atacama Desert (Conquest)",
                                "Levels/MP_006CQ": "Arica Harbor (Conquest)",
                                "Levels/MP_007": "White Pass (Conquest)",
                                "Levels/MP_009CQ": "Laguna Presa (Conquest)",
                                "Levels/MP_002": "Valparaiso (Rush)",
                                "Levels/MP_004": "Isla Inocentes (Rush)",
                                "Levels/MP_006": "Arica Harbor (Rush)",
                                "Levels/MP_008": "Nelson Bay (Rush)",
                                "Levels/MP_009GR": "Laguna Presa (Rush)",
                                "Levels/MP_012GR": "Port Valdez (Squad Rush)",
                                "Levels/MP_001SR": "Panama Canal (Squad Rush)",
                                "Levels/MP_002SR": "Valparaiso (Squad Rush)",
                                "Levels/MP_005SR": "Atacama Desert (Squad Rush)",
                                "Levels/MP_012SR": "Port Valdez (Squad Rush)",
                                "Levels/MP_004SDM": "Isla Inocentes (Squad Deathmatch)",
                                "Levels/MP_006SDM": "Arica Harbor (Squad Deathmatch)",
                                "Levels/MP_007SDM": "White Pass (Squad Deathmatch)",
                                "Levels/MP_009SDM": "Laguna Presa (Squad Deathmatch)"}
    mix_conquest_rush = True

    #pool of green threads for action
    action_pool = eventlet.GreenPool()

    #event logging queue
    event_log_q = eventlet.Queue()

    #btps log queue
    log_q = eventlet.Queue()

    #state management queue
    state_q = eventlet.Queue()

    #dictionary of commands with their functions
    cmds = {"!serveryell": serveryell,
            "!playeryell": playeryell,
            "!map": map_,
            "!kick": kick,
            "!ban": ban,
            "!gonext": gonext,
            "!playersay": playersay,
            "!serversay": serversay,
            "!ff": ff}

    #establish connection for event stream
    event_socket = _server_connect(host, port)
    _auth(event_socket, pw)
    _set_receive_events(event_socket)

    #establish connection for outgoing commands
    command_socket = _server_connect(host, port)
    _auth(command_socket, pw)

    #spawn threads
    action_pool.spawn_n(server_state)
    action_pool.spawn_n(irc_bot, ircHOST, ircPORT, ircNICK, ircIDENT, ircREALNAME, ircCHANNEL)
    action_pool.spawn_n(event_logger)
    action_pool.spawn_n(log_processor)
    #action_pool.spawn_n(server_manager)

    event_msg_prio = 2

    recv_buffer = ''

    process=True
    #process=False
    while process:
        #get packet
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
                    cmds[potential_cmd](talker, chat_words)
                else:
                    #don't want to respond to server-initiated chat
                    if talker != "Server":
                        chat_notice(chat_words)
