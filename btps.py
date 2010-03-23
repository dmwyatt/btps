import datetime
import hashlib
import os
import shlex
from struct import *

from pkg import bc2_misc
import eventlet
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

def event_logger_qer(event):
    ''' Implement mysql event logger
    '''
    global event_log_q
    event_log_q.put(event)
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
    screen_log("Event logger thread started")
    cfg = _get_mysql_config()
    db = mysql.connector.Connect(host=cfg['host'],
                             user=cfg['user'],
                             password=cfg['password'],
                             database=cfg['database'])
    cursor = db.cursor()

    stmt_create =   """
                    CREATE TABLE IF NOT EXISTS bc2_events (
                        id TINYINT UNSIGNED NOT NULL AUTO_INCREMENT,
                        event VARCHAR(30) DEFAULT '' NOT NULL,
                        info TEXT,
                        PRIMARY KEY (id)
                    )"""
    cursor.execute(stmt_create)

    screen_log("Starting event_logger loop")
    while True:
        try:
            evt = event_log_q.get()
        except:
            continue

        screen_log("FETCHED FROM EVENT QUEUE: %s" % evt)
        stmt_insert = "INSERT INTO bc2_events (event, info) VALUES (%s, %s)"
        try:
            cursor.execute(stmt_insert, (evt[0], ' '.join(evt[1:])))
            db.commit()
        except:
            screen_log("ERROR INSERTING INTO MYSQL")

def event_onchat(words):
    player = words[1]
    text = ' '.join(words[2:])
    stmt_insert = "INSERT INTO bc2_chat (player, text) VALUES (%s, %s)"
    cursor.execute(stmt_insert)

def create_tables():
    chat_create = """CREATE TABLE IF NOT EXISTS bc2_chat (
                        id INT UNSIGNED NOT NULL AUTO_INCREMENT,
                        player VARCHAR(50) DEFAULT '' NOT NULL,
                        chat TEXT,
                        PRIMARY KEY (id))
                        """
    #conx_create = """CREATE TABLE IF NOT EXISTS bc2_connections (
    #                    id INT UNSIGNED NOT NULL AUTO_INCREMENT,
    #                    player VARCHAR(50) DEFAULT '' NOT NULL,
    #                    join)"""
    soldier_info_create = """CREATE TABLE IF NOT EXISTS bc2_soldiers(
                                id INT UNSIGNED NOT NULL AUTO_INCREMENT,
                                soldier VARCHAR(50) DEFAULT '' NOT NULL,
                                pb_guid VARCHAR(50) DEFAULT '' NOT NULL,
                                PRIMARY KEY(id))
                                """

    db = mysql.connector.Connect(host=cfg['host'],
                             user=cfg['user'],
                             password=cfg['password'],
                             database=cfg['database'])
    cursor = db.cursor()

client_seq_number = 0

if __name__ == '__main__':
    #pool of green threads for action
    action_pool = eventlet.GreenPool()

    #queue of commands to send to BC2 server
    command_q = eventlet.Queue()

    #queue of events to log
    #event_log_q = eventlet.Queue()

    host = "75.102.38.3"
    port = 48888
    #f = open(os.path.join("..", "bc2_info.pw"),"r")
    #pw = f.read().strip()
    pw = open("config/password").read().strip()

    admins = open("config/admins").read().split("\n")
    print "ADMINS: %s" % admins
    cmds = {"!serveryell": serveryell,
            "!playeryell": playeryell,
            "!map": map_,
            "!kick": kick,
            "!kicksay": kicksay,
            "!ban": ban}

    event_socket = _server_connect(host, port)
    _auth(event_socket, pw)
    _set_receive_events(event_socket)

    command_socket = _server_connect(host, port)
    _auth(command_socket, pw)

    action_pool.spawn_n(command_processor)
    action_pool.spawn_n(event_logger)


    while True:
        #print log("waiting for packet")
        packet = event_socket.recv(4096)

        #decode packet
        #print log("decoding packet     ")
        _, is_response, sequence, words = bc2_misc._decode_pkt(packet)

        #ack packet
        if not is_response:
            response = bc2_misc._encode_resp(sequence, ["OK"])
            event_socket.send(response)

        #print log("received pkt with words (and initiated woo): ")
        m = "EVENT: %s PARAM: %s" % (words[0], words[1:])
        screen_log(m)
        #event_logger_qer(words)
        #print m
        #print '-'*30

        #process event
        if words[0] == 'player.onChat':
            try:
                chat_words = shlex.split(words[2])
            except:
                continue

            talker = words[1]
            potential_cmd = chat_words[0].lower()
            if potential_cmd in cmds:
                cmds[potential_cmd](talker, chat_words, command_socket)
