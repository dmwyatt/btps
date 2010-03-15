import hashlib
import os
import shlex
from struct import *
import socket

class CommandConnection():
    '''Objects of this type maintain a connection to a BC2 server'''
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
        self.serversocket.setblocking(1)

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
        request = self._encode_req(words)
        self.serversocket.send(request)

        # Wait for response from server
        packet = self.serversocket.recv(4096)
        [is_from_server, is_response, sequence, words] = self._decode_pkt(packet)
        # The packet from the server should
        # For now, we always respond with an "OK"
        if not is_response:
            print 'Received an unexpected request packet from server, ignored:'

        return words

    def _auth(self):
        #authentication
        # Retrieve this connection's 'salt'
        salt_req = self._encode_req(["login.hashed"])
        self.serversocket.send(salt_req)
        salt_req_response = self.serversocket.recv(4096)
        #[isFromServer, isResponse, sequence, words] = DecodePacket(getPasswordSaltResponse)
        is_from_server, is_response, sequence, words = self._decode_pkt(salt_req_response)

        # Given the salt and the password, combine them and compute hash value
        salt = words[1].decode("hex")
        pw_hash = self._hash_pw(salt, pw)
        pw_hash_encoded = pw_hash.encode("hex").upper()

        # Send password hash to server
        login_req = self._encode_req(["login.hashed", pw_hash_encoded])
        self.serversocket.send(login_req)
        login_resp = self.serversocket.recv(4096)
        #[isFromServer, isResponse, sequence, words] = self._decode_pkt(login_resp)
        is_from_server, is_response, sequence, words = self._decode_pkt(login_resp)

        # if the server didn't like our password, abort
        if words[0] != "OK":
            raise ValueError("Incorrect password")

    def _hash_pw(self, salt, password):
        m = hashlib.md5()
        m.update(salt)
        m.update(password)
        return m.digest()

    def _encode_req(self, words):
        packet = self._encode_pkt(False, False, self.client_seq, words)
        self.client_seq = (self.client_seq + 1) & 0x3fffffff
        return packet

    def _encode_pkt(self, is_from_server, is_response, sequence, words):
        enc_header = self._encode_header(is_from_server, is_response, sequence)
        enc_word_count = self._encode_int32(len(words))
        [words_size, enc_words] = self._encode_words(words)
        enc_size = self._encode_int32(words_size + 12)

        return enc_header + enc_size + enc_word_count + enc_words

    def _decode_pkt(self, data):
        [isFromServer, isResponse, sequence] = self._decode_header(data)
        words_size = self._decode_int32(data[4:8]) - 12
        words = self._decode_words(words_size, data[12:])
        return [isFromServer, isResponse, sequence, words]

    def _encode_header(self, is_from_server, is_response, sequence):
        header = sequence & 0x3fffffff

        if is_from_server:
            header += 0x80000000
        if is_response:
            header += 0x40000000

        return pack('<I', header)

    def _decode_header(self, data):
        [header] = unpack('<I', data[0 : 4])
        return [header & 0x80000000, header & 0x40000000, header & 0x3fffffff]

    def _encode_int32(self, size):
        return pack('<I', size)

    def _decode_int32(self,data):
        return unpack('<I', data[0 : 4])[0]

    def _encode_words(self, words):
        size = 0
        enc_words = ''

        for word in words:
            wrd = str(word)
            enc_words += self._encode_int32(len(wrd))
            enc_words += wrd
            enc_words += '\x00'
            size += len(wrd) + 5

        return size, enc_words

    def _decode_words(self, size, data):
        word_count = self._decode_int32(data[0:])
        words = []
        offset = 0
        while offset < size:
            word_length = self._decode_int32(data[offset : offset + 4])
            word = data[offset + 4 : offset + 4 + word_length]
            words.append(word)
            offset += word_length + 5

        return words


if __name__ == '__main__':
    host = "75.102.38.3"
    port = 48888
    #f = open(os.path.join("..", "..", "bc2_info.pw"),"r")
    #pw = f.read().strip()
    pw = "password"

    conx = CommandConnection(host, port, pw)