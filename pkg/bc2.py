import hashlib
import os
import shlex
from struct import *

import bc2_misc
import eventlet
from eventlet.green import socket


class CommandConnection():
    '''Objects of this type maintain a command connection to a BC2 server'''
    def __init__(self, host, port, pw):
        self.host = host
        self.port = port
        self.pw = pw
        self.client_seq = 0
        self._connect()
        if not self._is_connected():
            raise ValueError("Failed to connect to server")

    def disconnect(self):
        try:
            self.send_command('quit')
        except:
            pass

    def _connect(self):
        #open socket
        self.serversocket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.serversocket.connect((host, port))
        #self.serversocket.setblocking(1)

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
        self.serversocket.send(request)

        # Wait for response from server
        packet = self.serversocket.recv(4096)
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
        self.serversocket.send(salt_req)
        salt_req_response = self.serversocket.recv(4096)
        #[isFromServer, isResponse, sequence, words] = DecodePacket(getPasswordSaltResponse)
        is_from_server, is_response, sequence, words = bc2_misc._decode_pkt(salt_req_response)

        # Given the salt and the password, combine them and compute hash value
        salt = words[1].decode("hex")
        pw_hash = bc2_misc._hash_pw(salt, pw)
        pw_hash_encoded = pw_hash.encode("hex").upper()

        # Send password hash to server
        login_req, self.client_seq = bc2_misc._encode_req(["login.hashed", pw_hash_encoded], self.client_seq)
        self.serversocket.send(login_req)
        login_resp = self.serversocket.recv(4096)
        #[isFromServer, isResponse, sequence, words] = self._decode_pkt(login_resp)
        is_from_server, is_response, sequence, words = bc2_misc._decode_pkt(login_resp)

        # if the server didn't like our password, abort
        if words[0] != "OK":
            raise ValueError("Incorrect password")

    def receive_events(self):
        events_req, self.client_seq = bc2_misc._encode_req(["eventsEnabled", "true"], self.client_seq)
        self.serversocket.send(events_req)
        events_resp = self.serversocket.recv(4096)

        [is_from_server, is_response, sequence, words] = bc2_misc._decode_pkt(events_resp)

        while True:
            # Wait for packet from server
            print "getting packet"
            packet = self.serversocket.recv(4096)
            print "decoding packet"
            [is_from_server, is_response, sequence, words] = bc2_misc._decode_pkt(packet)
            # If this was a command from the server, we should respond to it
            # For now, we always respond with an "OK"
            if not is_response:
                response = bc2_misc._encode_resp(sequence, ["OK"])
                self.serversocket.send(response)
            else:
                pass
            print "---"
            print packet



if __name__ == '__main__':
    host = "75.102.38.3"
    port = 48888
    f = open(os.path.join("..", "..", "bc2_info.pw"),"r")
    pw = f.read().strip()

    conx = CommandConnection(host, port, pw)
    conx.receive_events()