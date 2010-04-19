#first we have to monkey-patch standard library to support green threads
import eventlet
eventlet.monkey_patch()

import datetime
import hashlib
import inspect
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
#Consumer loops/Threads
############################################################
def thread_manager():
    global thread_q
    thread_pool = eventlet.GreenPool()
    while 1:
        if thread_q.empty():
            eventlet.greenthread.sleep()
            continue

        #thread_msg = [funcname, action, thread message]
        thread_msg = thread_q.get()

        if thread_msg[1] == 'not_started':
            screen_log("Starting %s thread" % thread_msg[0], 2)
            thread_pool.spawn_n(threads[thread_msg[0]])
        elif thread_msg[1] == 'dead':
            log_m = "%s thread died (%s), restarting..." % (thread_msg[0], thread_msg[2])
            screen_log(log_m, 1)
            log_q.put(log_m)
            thread_pool.spawn_n(threads[thread_msg[0]])
        else:
            log_m = "Received invalid thread_q message: %s" % thread_msg
            screen_log(log_m, 1)
            log_q.put(log_m)

def receive_events():
    global thread_q
    global event_socket

    screen_log("BC2 event receiver started", 2)
    try:
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

                #check event for commands
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
                            #respond to player chat like "friendly fire"
                            chat_notice(chat_words)
    except:
        thread_q.put(funcname(), 'dead', 'unknown reason')
        return

def irc_bot():
    global thread_q
    global irc_out_qu

    def write_irc():
        global irc_out_qu
        while 1:
            if irc_out_qu.empty():
                eventlet.greenthread.sleep()
                continue

            out = irc_out_qu.get()
            s.send(out)


    readbuffer=""
    global_state['irc']['joined'] = False

    s = irc_connect(global_state['irc']['host'],
                    global_state['irc']['port'],
                    global_state['irc']['nick'],
                    global_state['irc']['ident'],
                    global_state['irc']['realname'])


    screen_log("IRC bot thread started")
    irc_pool = eventlet.GreenPool()
    irc_pool.spawn_n(write_irc)
    global_state['irc']['authed_users'] = []

    irc_cmds = {"!bc2_gonext": irc_bc2_gonext,
                "!q": irc_q,
                "!bc2_serveryell": irc_bc2_syell,
                "!bc2_playeryell": irc_bc2_pyell,
                "!bot_users": irc_bot_users,
                "!bc2_serversay": irc_bc2_ssay,
                "!bc2_playersay": irc_bc2_psay
                }

    try:
        while 1:
            try:
                readbuffer=readbuffer+s.recv(1024)
            except:
                screen_log("IRC FAIL: Reconnecting in 60 seconds", level=3)
                time.sleep(60)
                s = irc_connect(global_state['irc']['host'],
                                global_state['irc']['port'],
                                global_state['irc']['nick'],
                                global_state['irc']['ident'],
                                global_state['irc']['realname'])
                continue

            #split up our readbuffer
            temp = readbuffer.split("\n")
            #readbuffer should now contain everthing from last "\n" to the end
            readbuffer=temp.pop( )

            #process each irc message we've received so far
            for line in temp:
                #join our channel...some networks don't let you JOIN immediately
                if irc_jointest(line):
                    irc_out_qu.put("JOIN %s\r\n" % global_state['irc']['channel'])
                    eventlet.spawn_after(10, irc_say, "Type '!q' for server status", global_state['irc']['channel'])
                    eventlet.spawn_after(10, irc_say, "Type '!%s' for bot commands" % global_state['irc']['nick'], global_state['irc']['channel'])
                    global_state['irc']['joined'] = True

                prefix, command, args = irc_parsemsg(line)
                if prefix:
                    nick = prefix.split('!')[0]

                #print "RAW: %s\nPREFIX: %s\nCOMMAND: %s\nARGS: %s" % (line, prefix, command, args)
                #print '-'*78

                if command == "PING":
                    irc_out_qu.put("PONG %s\r\n" % args[0])

                elif command == "PRIVMSG":
                    msg_parse = args[1].split()
                    #handle clients authing to bot
                    if args[0] == global_state['irc']['nick']:
                        if msg_parse[0].lower() == 'auth':
                            irc_auth(nick, msg_parse)

                    #give help on '!botname'
                    if msg_parse[0].lower() == "!%s" % global_state['irc']['nick'].lower():
                        if len(msg_parse) == 1:
                            for cmd in irc_cmds:
                                irc_notice(cmd, nick)
                            irc_notice(" ", nick)
                            irc_notice(" Type '!%s <cmd>' for more info" % global_state['irc']['nick'], nick)
                        elif len(msg_parse) == 2:
                            try:
                                help = irc_cmds[msg_parse[1].lower()].__doc__.split("\n")
                                for line in  help:
                                    irc_notice(line, nick)
                            except KeyError:
                                irc_notice("Not valid command.", nick)
                            except AttributeError:
                                irc_notice("No help for that command", nick)


                    #see if we have a function to handle first word/command
                    try:
                        irc_cmds[msg_parse[0]](nick, args)
                    except:
                        pass

    except:
        thread_q.put((funcname(), 'dead', 'unknown reason'))
        return

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
    global irc_qu
    global state_q
    global thread_q

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
                #time.sleep(.1)
                eventlet.greenthread.sleep()
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
                irc_out_qu.put(irc_new_player(global_state['irc']['channel'], msg[1]))
                irc_out_qu.put(irc_server_filling(global_state['irc']['channel']))
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
        thread_q.put((funcname(), 'dead', 'unknown reason'))
        return

def log_processor():
    global thread_q
    global log_q
    screen_log("Database logger access thread started", 2)
    try:
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
                #time.sleep(.1)
                eventlet.greenthread.sleep()
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
    except:
        thread_q.put((funcname(), 'dead', 'unknown reason'))
        return

def event_logger():
    ''' Loops waiting for events to be in event_log_q and then logs them to
        mysql.
    '''
    global event_log_q
    global thread_q
    screen_log("Event logger thread started", 2)

    try:
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
                eventlet.greenthread.sleep()
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
    except:
        thread_q.put((funcname(), 'dead', 'unknown reason'))
        return

############################################################
#Command functions
############################################################
def ff(admin, words):
    ''' Command action.
        Words should be a list formatted like:
            ["!ff", "message"]
    '''
    if admin not in global_state['admins']:
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
    if admin not in global_state['admins']:
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
    if admin not in global_state['admins']:
        return

    player = words[1]
    msg = ' '.join(words[2:])

    player_name = select_player(player, get_players_names())

    if player_name[0] == 1:
        _playersay(admin, player_name[1])
        return
    elif player_name[0] == 2:
        _playersay(admin, player_name[1])
        return
    else:
        _playersay(player_name, msg)

def serveryell(admin, words):
    ''' Command action.
        Words should be a list formatted like:
            ["!serveryell", "message", "words", "are", "split", "or", "not"]

        If duration isn't included we default to 4 seconds.
    '''
    if admin not in global_state['admins']:
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
    if admin not in global_state['admins']:
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

    player_name = select_player(player, get_players_names())

    if player_name[0] == 1:
        _playersay(admin, player_name[1])
        return
    elif player_name[0] == 2:
        _playersay(admin, player_name[1])
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
    if admin not in global_state['admins']:
        return

    cmd = 'admin.runNextLevel'
    response = send_command(cmd)
    if response[0] != 'OK':
        log_q.put("Error running %s.  Got %s" % (cmd, response))
        _playersay(admin, "Error running next level")
        return False
    else:
        return True

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
    if admin not in global_state['admins']:
        return

    _players = get_players_names()

    player_name = select_player(words[1], _players)

    msg = ' '.join(words[2:])

    if player_name[0] == 1:
        _playersay(admin, player_name[1])
        return
    elif player_name[0] == 2:
        _playersay(admin, player_name[1])
        return
    else:
        if player_name not in global_state['admins']:
            _kick(player_name, msg = msg)
            _playersay(admin, "ADMIN: Kicking %s." % player_name)
        else:
            _playersay(admin, "ADMIN: Can't kick admins.")



def ban(admin, words):
    if admin not in global_state['admins']:
        return

    _players = get_players_names()

    player_name = select_player(words[1], _players)

    parsed = _get_variable(words, 'd')

    if parsed:
        msg = ' '.join(parsed[0][2:])
        duration = int(parsed[1])
    else:
        msg = ' '.join(words[2:])
        duration = 'perm'

    if player_name[0] == 1:
        _playersay(admin, player_name[1])
        return
    elif player_name[0] == 2:
        _playersay(admin, player_name[1])
        return
    else:
        if player_name not in global_state['admins']:
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
    playeryell(global_state['sys_admin'], ["!playeryell", 5, "Therms", "BEGINNING COUNTDOWN"], skt)
    time.sleep(5)
    for i in reversed(range(seconds)):
        msg_mod = "%s (%i seconds)" % (msg, i)
        words = ["!playeryell", 1, player, msg_mod]
        playeryell(global_state['sys_admin'], words)
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

def select_player(player, players):
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
        #_playersay(admin, 'ADMIN: Be more specific with playername.')
        return (1, 'Be more specific with playername.')
    elif len(matches) == 0:
        #No matches
        #_playersay(admin, 'ADMIN: No matching playername.')
        return (2, 'No matching playername.')
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
def funcname():
    ''' Returns the name of the calling function
    '''
    return inspect.stack()[1][3]
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

            if global_state['mix_gametypes']:
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

        #new_playlist_maps = global_state['playlists'][new_playlist]['maps']
        #new_playlist_lastplayed = global_state['playlists'][new_playlist]['last_played']
        #new_maplist = new_playlist_maps[new_playlist_lastplayed+1:]
        #new_maplist.extend(new_playlist_maps[:new_playlist_lastplayed+1])

        change_maplist(global_state['playlists'][new_playlist]['maps'])

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
    if level <= global_state['screen_log_level']:
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

def _get_irc_config():
    config = open('config\\irc', 'r').read().split("\n")
    global_state['irc'] = {}
    for c in config:
        split = shlex.split(c)
        global_state['irc'][split[0][:-1]] = split[1]
    for setting in global_state['irc']:
        try:
            global_state['irc'][setting] = int(global_state['irc'][setting])
        except:
            continue

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
def irc_jointest(line):
    if global_state['irc']['joined']:
        return False

    hostsplit = global_state['irc']['host'].split(".")
    for component in hostsplit:
        if component.lower() in line.lower():
            return True
    return False

def irc_bot_users(nick, args):
    if irc_is_authed(nick):
        try:
            _users = global_state['irc']['authed_users']
        except:
            _users = "None"
        irc_notice("Users: %s" % _users, nick)

def irc_is_authed(nick):
    if nick in global_state['irc']['authed_users']:
        return True
    irc_not_authed(nick)
    return False
def irc_auth(nick, auth_msg):
    if len(auth_msg) != 2:
        irc_notice("Invalid auth", nick)
    elif auth_msg[1] == global_state['rcon_pass']:
        try:
            if nick not in global_state['irc']['authed_users']:
                global_state['irc']['authed_users'].append(nick)
        except:
            global_state['irc']['authed_users'] = [nick]
        irc_notice("Authed.", nick)
    else:
        irc_notice("Invalid auth", nick)

def irc_not_authed(nick):
    ''' Convenience function
    '''
    irc_notice("Command requires auth", nick)
    irc_notice("(/msg %s AUTH rcon_password)" % global_state['irc']['nick'], nick)

def irc_bc2_gonext(nick, args):
    ''' !bc2_gonext
            -changes to next map
    '''
    if irc_is_authed(nick):

        if gonext(global_state['sys_admin'], ['!gonext']):
            irc_notice("Changed map", nick)
        else:
            irc_notice("Map change fail.", nick)

def irc_bc2_syell(nick, args):
    ''' !bc2_syell message to server
            -Yells message to whole server
    '''
    if irc_is_authed(nick):
        msg_parse = args[1].split()
        if _serveryell(' '.join(msg_parse[1:]), 8):
            irc_notice("Yelled to server", nick)
        else:
            irc_notice("Yell fail", nick)

def irc_bc2_ssay(nick, args):
    ''' !bc2_ssay message to server
            -Says a message to whole server
    '''
    if irc_is_authed(nick):
        msg_parse = args[1].split()
        if _serversay(' '.join(msg_parse[1:])):
            irc_notice("Said text to server", nick)
        else:
            irc_notice("Serversay fail", nick)

def irc_bc2_pyell(nick, args):
    ''' !bc2_playeryell nick message to player
            -Yells message to specific player
    '''
    if irc_is_authed(nick):
        msg_parse = args[1].split()

        yell_to = select_player(msg_parse[1], get_players_names())
        if yell_to[0] == 1:
            irc_notice(yell_to[1], nick)
            return
        elif yell_to[0] == 2:
            irc_notice(yell_to[1], nick)
            return
        else:
            if _playeryell(yell_to, ' '.join(msg_parse[2:]), 8):
                irc_notice("Yell fail", nick)
            else:
                irc_notice("Yelled to %s" % yell_to)

def irc_bc2_psay(nick, args):
    ''' !bc2_playersay nick message to player
            -Says message to specific player
    '''
    if irc_is_authed(nick):
        msg_parse = args[1].split()

        say_to = select_player(msg_parse[1], get_players_names())
        if say_to[0] == 1:
            irc_notice(say_to[1], nick)
            return
        elif say_to[0] == 2:
            irc_notice(say_to[1], nick)
            return
        else:
            if _playersay(say_to, ' '.join(msg_parse[2:])):
                irc_notice("Yell fail", nick)
            else:
                irc_notice("Yelled to %s" % say_to)

def irc_say(msg, recipient):
    global irc_out_qu

    irc_out_qu.put("PRIVMSG %s :%s\r\n" % (recipient, msg))

def irc_notice(msg, recipient):
    global irc_out_qu

    irc_out_qu.put("NOTICE %s :%s\r\n" % (recipient, msg))

def irc_server_filling(channel):
    level1 = 9
    level2 = 10
    level3 = 11
    level4 = 12
    failcolor = 13

    players = len(global_state['players'])
    if players == 8:
        msg = irc_colorize("Server is starting to fill up.", level1)
    elif players == 12:
        msg = irc_colorize("Not kidding.  Server is filling.", level2)
    elif players == 18:
        msg = irc_colorize("No, really, the server has got people playing", level3)
    elif players == 27:
        msg = irc_colorize("Last chance to play on server, only a few slots left", level4)
    elif players == 32:
        msg = irc_colorize("You people fail at joining.  Server is now FULL.", failcolor)
    else:
        return

    players = irc_colorize("(%s/%s)" % (len(global_state['players']), global_state['maxplayers']), level3)

    msg = "%s %s" % (msg, players)
    msg = "PRIVMSG %s :%s\r\n" % (channel, msg)

    return msg


def irc_new_player(channel, player):
    #colors
    ctrl = "\x03"
    greenish = 10
    red = 4
    teal = 11

    newp = irc_colorize("Player joined BC2 server:", greenish)
    player = irc_colorize(player, red)
    players = irc_colorize("(%s/%s)" % (len(global_state['players']), global_state['maxplayers']), teal)
    msg = "%s %s %s" % (newp, player, players)
    msg = "PRIVMSG %s :%s\r\n" % (channel, msg)

    return msg

def irc_colorize(text, color_num):
    ctrl = "\x03"
    t = "%s%s%s%s" % (ctrl, color_num, text, ctrl)
    return t

def irc_q(nick, args):
    channel = args[0]
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

    irc_out_qu.put(msg)

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



client_seq_number = 0
global_state = {}
threads = {}

threads['irc_bot'] = irc_bot
threads['server_state'] = server_state
threads['event_logger'] = event_logger
threads['log_processor'] = log_processor
threads['receive_events'] = receive_events


if __name__ == '__main__':
    #config
    global_state['gameserver'] = {}
    global_state['gameserver']['host'] = "68.232.176.204"
    #host="75.102.38.3"
    global_state['gameserver']['port'] = 48888

    global_state['rcon_pass'] = open("config/password").read().strip()

    global_state['admins'] = open("config/admins").read().split("\n")
    global_state['sys_admin'] = str(uuid.uuid4())
    global_state['admins'].append(global_state['sys_admin'])
    global_state['screen_log_level'] = 3

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
    #global_state['irc'] = {"host": "irc.us.gamesurge.net",
    #                       "port": 6667,
    #                       "nick": "SmackBot",
    #                       "ident": "sbot",
    #                       "realname": "Thermsbot",
    #                       "channel": "#clan_cia"}
    _get_irc_config()

    global_state['mix_gametypes'] = True

    #pool of green threads for action
    action_pool = eventlet.GreenPool()

    #event logging queue
    event_log_q = eventlet.Queue()

    #btps log queue
    log_q = eventlet.Queue()

    #state management queue
    state_q = eventlet.Queue()

    #irc out queue
    irc_out_qu = eventlet.Queue()

    #thread management queue
    thread_q = eventlet.Queue()

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
    event_socket = _server_connect(global_state['gameserver']['host'], global_state['gameserver']['port'])
    _auth(event_socket, global_state['rcon_pass'])
    _set_receive_events(event_socket)

    #establish connection for outgoing commands
    command_socket = _server_connect(global_state['gameserver']['host'], global_state['gameserver']['port'])
    _auth(command_socket, global_state['rcon_pass'])

    #spawn threads
    thread_q.put(['server_state', 'not_started'])
    thread_q.put(['irc_bot', 'not_started'])
    thread_q.put(['event_logger', 'not_started'])
    thread_q.put(['log_processor', 'not_started'])
    thread_q.put(['receive_events', 'not_started'])
    action_pool.spawn_n(thread_manager)

    event_msg_prio = 2

    while 1:
        eventlet.greenthread.sleep()