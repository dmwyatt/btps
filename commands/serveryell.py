cmd = '!serveryell'

def serveryell():
    try:
        seconds = int(words[1])
        no_duration = False
    except:
        seconds = 4
        no_duration = True

    if no_duration:
        msg = ' '.join(words[1:])
    else:
        msg = ' '.join(words[2:])

    cmd = 'admin.yell "%s" %s all' % (msg, seconds*1000)

    return cmd