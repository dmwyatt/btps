import datetime
import hashlib
import os
import shlex
from struct import *

import bc2_misc
import eventlet
from eventlet.green import socket
from eventlet.green import time


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
    salt_req, client_seq_number = bc2_misc._encode_req(["login.hashed"], client_seq_number)
    skt.send(salt_req)
    salt_req_response = skt.recv(4096)
    #[isFromServer, isResponse, sequence, words] = DecodePacket(getPasswordSaltResponse)
    is_from_server, is_response, sequence, words = bc2_misc._decode_pkt(salt_req_response)

    # Given the salt and the password, combine them and compute hash value
    salt = words[1].decode("hex")
    pw_hash = bc2_misc._hash_pw(salt, pw)
    pw_hash_encoded = pw_hash.encode("hex").upper()

    # Send password hash to server
    login_req, client_seq_number = bc2_misc._encode_req(["login.hashed", pw_hash_encoded], client_seq_number)
    skt.send(login_req)
    login_resp = skt.recv(4096)
    #[isFromServer, isResponse, sequence, words] = self._decode_pkt(login_resp)
    _, _, _, words = bc2_misc._decode_pkt(login_resp)

    # if the server didn't like our password, abort
    if words[0] != "OK":
        raise ValueError("Incorrect password")

    return skt

def send_command(skt, cmd):
    words = shlex.split(cmd)
    request, client_seq_number = bc2_misc._encode_req(words, client_seq_number)
    skt.send(request)

    # Wait for response from server
    packet = skt.recv(4096)
    _, _, _, words = bc2_misc._decode_pkt(packet)

    if not is_response:
        print 'Received an unexpected request packet from server, ignored:'

    return words

def _set_receive_events(skt):
    send_command("eventsEnabled True")
    #events_req, client_seq_number = bc2_misc._encode_req(["eventsEnabled", "true"], client_seq_number)
    #skt.send(events_req)
    #events_resp = skt.recv(4096)

    #_, _, _, _ = bc2_misc._decode_pkt(events_resp)

    #return skt

def sleepy():
    time.sleep(100)
    print 'got a packet and slept'

def server_yell(skt):
    cmd = 'admin.yell cockface 4000 all'
    response, client_seq_number = send_command(skt, cmd, client_seq_number)

if __name__ == '__main__':
    client_seq_number = 0
    host = "75.102.38.3"
    port = 48888
    f = open(os.path.join("..", "..", "bc2_info.pw"),"r")
    pw = f.read().strip()

    admins = ['Therms']
    cmds = {"!serveryell": 'server_yell'}

    event_socket = _server_connect(host, port)
    event_socket, event_seq = _auth(event_socket, pw)
    event_socket, event_seq = _set_receive_events(event_socket, event_seq)

    pool = eventlet.GreenPool()
    while True:
        #receive packet
        packet = event_socket.recv(4096)

        #decode packet
        _, is_response, sequence, words = bc2_misc._decode_pkt(packet)

        #ack packet
        if not is_response:
            response = bc2_misc._encode_resp(sequence, ["OK"])
            event_socket.send(response)

        print "received pkt with words (and initiated woo): "
        print words
        print '-'*30

        #command packet?
        if words[0] == 'player.onChat' and words[1] in admins:
            chat_words = shlex.split(words[2])
            if chat_words[0] in cmds:
                pool.spawn_n(eval(cmds["!yell"]), event_socket, event_seq)

        pool.spawn_n(sleepy)