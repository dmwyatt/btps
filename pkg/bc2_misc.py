#!/usr/bin/env python
import hashlib
import shlex
from struct import *

def _hash_pw(salt, password):
    m = hashlib.md5()
    m.update(salt)
    m.update(password)
    return m.digest()

def _encode_req(words, client_seq):
    packet = _encode_pkt(False, False, client_seq, words)
    client_seq = (client_seq + 1) & 0x3fffffff
    return packet, client_seq

def _encode_pkt(is_from_server, is_response, sequence, words):
    enc_header = _encode_header(is_from_server, is_response, sequence)
    enc_word_count = _encode_int32(len(words))
    [words_size, enc_words] = _encode_words(words)
    enc_size = _encode_int32(words_size + 12)

    return enc_header + enc_size + enc_word_count + enc_words

def _decode_pkt(data):
    [isFromServer, isResponse, sequence] = _decode_header(data)
    words_size = _decode_int32(data[4:8]) - 12
    words = _decode_words(words_size, data[12:])
    return [isFromServer, isResponse, sequence, words]

def _encode_header(is_from_server, is_response, sequence):
    header = sequence & 0x3fffffff

    if is_from_server:
        header += 0x80000000
    if is_response:
        header += 0x40000000

    return pack('<I', header)

def _decode_header(data):
    [header] = unpack('<I', data[0 : 4])
    return [header & 0x80000000, header & 0x40000000, header & 0x3fffffff]

def _encode_int32(size):
    return pack('<I', size)

def _decode_int32(data):
    return unpack('<I', data[0 : 4])[0]

def _encode_words(words):
    size = 0
    enc_words = ''

    for word in words:
        wrd = str(word)
        enc_words += _encode_int32(len(wrd))
        enc_words += wrd
        enc_words += '\x00'
        size += len(wrd) + 5

    return size, enc_words

def _decode_words(size, data):
    word_count = _decode_int32(data[0:])
    words = []
    offset = 0
    while offset < size:
        word_length = _decode_int32(data[offset : offset + 4])
        word = data[offset + 4 : offset + 4 + word_length]
        words.append(word)
        offset += word_length + 5

    return words

