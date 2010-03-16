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
    client_seq_number = 0
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

    return skt, client_seq_number

def send_command(skt, cmd, client_seq_number):
    words = shlex.split(cmd)
    request, client_seq_number = bc2_misc._encode_req(words, client_seq_number)
    skt.send(request)

    # Wait for response from server
    packet = skt.recv(4096)
    _, _, _, words = bc2_misc._decode_pkt(packet)

    if not is_response:
        print 'Received an unexpected request packet from server, ignored:'

    return words, client_seq_number

def _set_receive_events(skt, client_seq_number):
    events_req, client_seq_number = bc2_misc._encode_req(["eventsEnabled", "true"], client_seq_number)
    skt.send(events_req)
    events_resp = skt.recv(4096)

    _, _, _, _ = bc2_misc._decode_pkt(events_resp)

    return skt, client_seq_number

def _wait_for_event_pkts(skt, client_seq_number):
    while True:
        # Wait for packet from server
        print "getting packet"
        packet = skt.recv(4096)
        print "decoding packet"
        #packet_q.put(packet)
        [_, is_response, sequence, words] = bc2_misc._decode_pkt(packet)
        # If this was a command from the server, we should respond to it
        # For now, we always respond with an "OK"
        if not is_response:
            response = bc2_misc._encode_resp(sequence, ["OK"])
            skt.send(response)
        else:
            pass
        print "---"
        print _, is_response, sequence, words

def _decode_pkts():
    while True:
        pass



def sleepy():
    time.sleep(100)
    print 'got a packet and slept'


class CommandConnection():
    '''Objects of this type maintain a command connection to a BC2 server'''
    def __init__(self, host, port, pw):
        self.host = host
        self.port = port
        self.pw = pw
        self.client_seq = 0
        self._connect_cmd()
        if not self._is_connected():
            raise ValueError("Failed to connect to server")

    def disconnect(self):
        try:
            self.send_command('quit')
        except:
            pass

    def _connect_cmd(self):
        #open socket
        self.server_cmd_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.server_cmd_socket.connect((host, port))
        #self.server_cmd_socket.setblocking(1)

        #authentication
        self._auth()

    def _is_connected(self):
        try:
            version = self.send_command("version")
        except:
            return False
        if version[0] == 'OK':
            return True
        return False

    def send_command(self, cmd):
        words = shlex.split(cmd)
        request, self.client_seq = bc2_misc._encode_req(words, self.client_seq)
        self.server_cmd_socket.send(request)

        # Wait for response from server
        packet = self.server_cmd_socket.recv(4096)
        [is_from_server, is_response, sequence, words] = bc2_misc._decode_pkt(packet)
        # The packet from the server should
        # For now, we always respond with an "OK"
        if not is_response:
            print 'Received an unexpected request packet from server, ignored:'

        return words

    def _auth(self):
        #authentication
        # Retrieve this connection's 'salt'
        salt_req, self.client_seq = bc2_misc._encode_req(["login.hashed"], self.client_seq)
        self.server_cmd_socket.send(salt_req)
        salt_req_response = self.server_cmd_socket.recv(4096)
        #[isFromServer, isResponse, sequence, words] = DecodePacket(getPasswordSaltResponse)
        is_from_server, is_response, sequence, words = bc2_misc._decode_pkt(salt_req_response)

        # Given the salt and the password, combine them and compute hash value
        salt = words[1].decode("hex")
        pw_hash = bc2_misc._hash_pw(salt, pw)
        pw_hash_encoded = pw_hash.encode("hex").upper()

        # Send password hash to server
        login_req, self.client_seq = bc2_misc._encode_req(["login.hashed", pw_hash_encoded], self.client_seq)
        self.server_cmd_socket.send(login_req)
        login_resp = self.server_cmd_socket.recv(4096)
        #[isFromServer, isResponse, sequence, words] = self._decode_pkt(login_resp)
        is_from_server, is_response, sequence, words = bc2_misc._decode_pkt(login_resp)

        # if the server didn't like our password, abort
        if words[0] != "OK":
            raise ValueError("Incorrect password")

    def _set_receive_events(self):
        events_req, self.client_seq = bc2_misc._encode_req(["eventsEnabled", "true"], self.client_seq)
        self.server_cmd_socket.send(events_req)
        events_resp = self.server_cmd_socket.recv(4096)

        [is_from_server, is_response, sequence, words] = bc2_misc._decode_pkt(events_resp)

        while True:
            # Wait for packet from server
            print "getting packet"
            packet = self.server_cmd_socket.recv(4096)
            print "decoding packet"
            [is_from_server, is_response, sequence, words] = bc2_misc._decode_pkt(packet)
            # If this was a command from the server, we should respond to it
            # For now, we always respond with an "OK"
            if not is_response:
                response = bc2_misc._encode_resp(sequence, ["OK"])
                self.server_cmd_socket.send(response)
            else:
                pass
            print "---"
            print packet


if __name__ == '__main__':
    host = "75.102.38.3"
    port = 48888
    f = open(os.path.join("..", "..", "bc2_info.pw"),"r")
    pw = f.read().strip()

    #conx = CommandConnection(host, port, pw)
    #conx.receive_events()

    command_socket = _server_connect(host, port)
    command_socket, command_seq = _auth(command_socket, pw)

    event_socket = _server_connect(host, port)
    event_socket, event_seq = _auth(event_socket, pw)
    event_socket, event_seq = _set_receive_events(event_socket, event_seq)

    pool = eventlet.GreenPool()
    while True:
        packet = event_socket.recv(4096)
        _, is_response, sequence, words = bc2_misc._decode_pkt(packet)
        if not is_response:
            response = bc2_misc._encode_resp(sequence, ["OK"])
            event_socket.send(response)
        print "received pkt with words (and initiated woo): "
        print words
        print '-'*30
        pool.spawn_n(sleepy)


    #_wait_for_event_pkts(event_socket, event_seq)