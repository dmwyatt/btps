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
    global client_seq_number
    #print "send_command pre CLIENT_SEQ: %i" % client_seq_number
    words = shlex.split(cmd)
    request, client_seq_number = bc2_misc._encode_req(words, client_seq_number)
    #print "send_command post CLIENT_SEQ: %i" % client_seq_number
    skt.send(request)

    # Wait for response from server
    packet = skt.recv(4096)
    _, is_response, _, words = bc2_misc._decode_pkt(packet)

    if not is_response:
        print 'Received an unexpected request packet from server, ignored:'

    #print cmd, words

    return words

def prep_admin_command(cmd):
    global client_seq_number
    words = shlex.split(cmd)
    request, client_seq_number = bc2_misc._encode_req(words, client_seq_number)
    return request

def _set_receive_events(skt):
    send_command(skt, "eventsEnabled true")

def sleepy():
    time.sleep(100)
    print 'got a packet and slept'

def serveryell(words):
    global command_q
    try:
        seconds = int(words[1])/1000
        no_duration = False
    except:
        seconds = 4
        no_duration = True

    if no_duration:
        msg = ' '.join(words[1:])
    else:
        msg = ' '.join(words[2:])


    #cmd = 'admin.yell testtesttest 4000 all'
    cmd = 'admin.yell "%s" %s all' % (msg, seconds*1000)
    print "COMMAND_Q: %s" % cmd
    command_q.put(cmd)

def command_processor():
    global command_socket
    while True:
        try:
            cmd = command_q.get()
            #print "SENDING COMMAND: %s" % cmd
        except:
            continue
        send_command(command_socket, cmd)

client_seq_number = 0

if __name__ == '__main__':
    #pool of green threads for action
    action_pool = eventlet.GreenPool()

    #queue of commands to send to BC2 server
    command_q = eventlet.Queue()

    host = "75.102.38.3"
    port = 48888
    f = open(os.path.join("..", "bc2_info.pw"),"r")
    pw = f.read().strip()

    admins = ['Therms', 'Fatb']
    cmds = {"!serveryell": serveryell}

    event_socket = _server_connect(host, port)
    _auth(event_socket, pw)
    _set_receive_events(event_socket)

    command_socket = _server_connect(host, port)
    _auth(command_socket, pw)

    action_pool.spawn_n(command_processor)

    #print 'starting'
    while True:
        #if command_q.qsize() > 1:
        #    print "cmd_q size: %i" %command_q.qsize()
        #receive packet
        print 'receiving packet'
        packet = event_socket.recv(4096)
        print 'got packet'

        #decode packet
        print 'decoding packet'
        _, is_response, sequence, words = bc2_misc._decode_pkt(packet)

        #ack packet
        if not is_response:
            response = bc2_misc._encode_resp(sequence, ["OK"])
            event_socket.send(response)

        print "received pkt with words (and initiated woo): "
        print words
        print '-'*30

        #process event
        if words[0] == 'player.onChat' and words[1] in admins:
            try:
                chat_words = shlex.split(words[2])
            except:
                continue
            print "CHAT WORDS: %s" % chat_words
            potential_cmd = chat_words[0].lower()
            if potential_cmd in cmds:
                cmds[potential_cmd](chat_words)
