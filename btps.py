import datetime
import hashlib
import os
import shlex
from struct import *

from pkg import bc2_misc
import eventlet
from eventlet.green import socket
from eventlet.green import time
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

    #Get message to send from words
    if no_duration:
        msg = words[1]
    else:
        msg = words[2]

    #Build command.  Duration is in ms
    cmd = 'admin.yell "%s" %s all' % (msg, seconds*1000)

    #insert command into command queue
    command_q.put(cmd)

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
        command_q.put(cmd)

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

    command_q.put(cmd)

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
            cmd = 'admin.kickPlayer %s' % player_name
            command_q.put(cmd)
        else:
            playeryell(admin, ['!playeryell', admin, "ADMIN: Can't kick admins."], skt)




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
    print "_PLAYERS: %s" % _players
    players = [x[1] for x in _players]

    #filter the list for just the names
    #field_count = 0
    #players_l = []
    #p =[]
    #for player in players[1:]:
    #    p.append(player)
    #    field_count += 1
    #    if field_count == 4:
    #            field_count = 0
    #            players_l.append(tuple(p))
    #            p = []
    #
    #players = [x[1] for x in players_l]

    return players

def select_player(player, players, admin, skt):
    ''' Selects a player from a list of players.  Can use player name substrings.
        If substring isn't unique enough, or if no matches are found, we'll
        message the admin who initiated the command informing them of this.
    '''
    #print "FINDING %s AMONGST %s BY %s" % (player, players, admin)
    #Find player amongst players
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
    while True:
        try:
            cmd = command_q.get()
        except:
            continue
        send_command(command_socket, cmd)

def log(msg):
    dt = datetime.datetime.today().strftime("%m/%d/%y %H:%M:%S")
    print "%s - %s" % (dt, msg)

client_seq_number = 0

if __name__ == '__main__':
    #pool of green threads for action
    action_pool = eventlet.GreenPool()

    #queue of commands to send to BC2 server
    command_q = eventlet.Queue()

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
            "!kick": kick}

    event_socket = _server_connect(host, port)
    _auth(event_socket, pw)
    _set_receive_events(event_socket)

    command_socket = _server_connect(host, port)
    _auth(command_socket, pw)

    action_pool.spawn_n(command_processor)


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
        log(m)
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
