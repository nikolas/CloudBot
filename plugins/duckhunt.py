import operator
import random
from collections import defaultdict
from threading import Lock
from time import time, sleep

from sqlalchemy import Table, Column, String, Integer, PrimaryKeyConstraint, desc, Boolean
from sqlalchemy.sql import select

from cloudbot import hook
from cloudbot.event import EventType
from cloudbot.util import database
from cloudbot.util.formatting import pluralize_auto

word_file = "/usr/share/dict/words"
WORDS = open(word_file).read().splitlines()

duck_tails = [
    "｡･ ﾟ･｡* ｡ +ﾟ｡･.｡* ﾟ + ｡･ﾟ･",
    "・゜゜・。。・゜゜",
    "・ ✺ 。。•・゜・。。 ✺ ・•",
    "。。・゜º•・。゜ ✺ ゜",
    "*・゜ﾟ・*:.｡ ｡.:*・゜ﾟ・*",
]

duck = [
    "\_ø< ", "\_Ø< ",
    "\_o< ", "\_O< ", "\_0< ",
    "\_O_o< ",
    "\_o_O< ",
    "\_č ",
    "\_c ",
    "\_ç ",
    "\_ǫ ",
    "\_Ǫ ",
    "\_\u00f6< ", "\_\u00f8< ", "\_\u00f3< "
]
duck_noise = [
    "QUACK!",
    "FLAP FLAP!",
    "quack!",
    "quaAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAACK!",
    "QuAaAaAACk",
    "QUAHFINNZKNJZCCK",
    "PIZZA NOT MOTA",
    "THEY'RE TRASHING OUR RIGHTS"
]

table = Table(
    'duck_hunt',
    database.metadata,
    Column('network', String),
    Column('name', String),
    Column('shot', Integer),
    Column('befriend', Integer),
    Column('chan', String),
    PrimaryKeyConstraint('name', 'chan', 'network')
)

optout = Table(
    'nohunt',
    database.metadata,
    Column('network', String),
    Column('chan', String),
    PrimaryKeyConstraint('chan', 'network')
)

status_table = Table(
    'duck_status',
    database.metadata,
    Column('network', String),
    Column('chan', String),
    Column('active', Boolean, default=False),
    Column('duck_kick', Boolean, default=False),
    PrimaryKeyConstraint('network', 'chan')
)

"""
game_status structure
{
    'network':{
        '#chan1':{
            'duck_status':0|1|2,
            'next_duck_time':'integer',
            'game_on':0|1,
            'no_duck_kick': 0|1,
            'duck_time': 'float',
            'shoot_time': 'float',
            'messages': integer,
            'masks' : list
        }
    }
}
"""

MSG_DELAY = 10
MASK_REQ = 3
scripters = defaultdict(int)
chan_locks = defaultdict(lambda: defaultdict(Lock))
game_status = defaultdict(lambda: defaultdict(lambda: defaultdict(int)))


@hook.on_start()
def load_optout(db):
    """load a list of channels duckhunt should be off in. Right now I am being lazy and not
    differentiating between networks this should be cleaned up later."""
    global opt_out
    opt_out = []
    chans = db.execute(select([optout.c.chan]))
    if chans:
        for row in chans:
            chan = row["chan"]
            opt_out.append(chan)


@hook.on_start
def load_status(db):
    rows = db.execute(status_table.select())
    for row in rows:
        net = row['network']
        chan = row['chan']
        status = game_status[net][chan]
        status["game_on"] = int(row['active'])
        status["no_duck_kick"] = int(row['duck_kick'])
        set_ducktime(chan, net)


def save_channel_state(db, network, chan, status=None):
    if status is None:
        status = game_status[network][chan.casefold()]

    active = bool(status['game_on'])
    duck_kick = bool(status['no_duck_kick'])
    res = db.execute(status_table.update().where(status_table.c.network == network).where(
        status_table.c.chan == chan).values(
        active=active, duck_kick=duck_kick
    ))
    if not res.rowcount:
        db.execute(status_table.insert().values(network=network, chan=chan, active=active, duck_kick=duck_kick))

    db.commit()


@hook.on_unload
def save_on_exit(db):
    return save_status(db, False)


@hook.periodic(8 * 3600, singlethread=True)  # Run every 8 hours
def save_status(db, _sleep=True):
    for network in game_status:
        for chan, status in game_status[network].items():
            save_channel_state(db, network, chan, status)

            if _sleep:
                sleep(5)


def set_game_state(db, conn, chan, active=None, duck_kick=None):
    status = game_status[conn.name][chan]
    if active is not None:
        status['game_on'] = int(active)

    if duck_kick is not None:
        status['no_duck_kick'] = int(duck_kick)

    save_channel_state(db, conn.name, chan, status)


@hook.event([EventType.message, EventType.action], singlethread=True)
def incrementMsgCounter(event, conn):
    """Increment the number of messages said in an active game channel. Also keep track of the unique masks that are speaking."""
    global game_status
    if event.chan in opt_out:
        return
    if game_status[conn.name][event.chan]['game_on'] == 1 and game_status[conn.name][event.chan]['duck_status'] == 0:
        game_status[conn.name][event.chan]['messages'] += 1
        if event.host not in game_status[conn.name][event.chan]['masks']:
            game_status[conn.name][event.chan]['masks'].append(event.host)


@hook.command("starthunt", autohelp=False, permissions=["chanop", "op", "botcontrol"])
def start_hunt(db, chan, message, conn):
    """- This command starts a duckhunt in your channel, to stop the hunt use .stophunt"""
    global game_status
    if chan in opt_out:
        return
    elif not chan.startswith("#"):
        return "No hunting by yourself, that isn't safe."
    check = game_status[conn.name][chan]['game_on']
    if check:
        return "there is already a game running in {}.".format(chan)
    else:
        set_game_state(db, conn, chan, active=True)

    set_ducktime(chan, conn.name)
    message(
        "Ducks have been spotted nearby. See how many you can shoot or save. use .bang to shoot or .befriend to save them. NOTE: Ducks now appear as a function of time and channel activity.",
        chan)


def set_ducktime(chan, conn):
    global game_status
    game_status[conn][chan]['next_duck_time'] = random.randint(int(time()) + 880, int(time()) + 8600)
    # game_status[conn][chan]['flyaway'] = game_status[conn.name][chan]['next_duck_time'] + 600
    game_status[conn][chan]['duck_status'] = 0
    # let's also reset the number of messages said and the list of masks that have spoken.
    game_status[conn][chan]['messages'] = 0
    game_status[conn][chan]['masks'] = []
    return


@hook.command("stophunt", autohelp=False, permissions=["chanop", "op", "botcontrol"])
def stop_hunt(db, chan, conn):
    """- This command stops the duck hunt in your channel. Scores will be preserved"""
    global game_status
    if chan in opt_out:
        return
    if game_status[conn.name][chan]['game_on']:
        set_game_state(db, conn, chan, active=False)
        return "the game has been stopped."
    else:
        return "There is no game running in {}.".format(chan)


@hook.command("duckkick", permissions=["chanop", "op", "botcontrol"])
def no_duck_kick(db, text, chan, conn, notice_doc):
    """<enable|disable> - If the bot has OP or half-op in the channel you can specify .duckkick enable|disable so that people are kicked for shooting or befriending a non-existent goose. Default is off."""
    global game_status
    if chan in opt_out:
        return
    if text.lower() == 'enable':
        set_game_state(db, conn, chan, duck_kick=True)
        return "users will now be kicked for shooting or befriending non-existent ducks. The bot needs to have appropriate flags to be able to kick users for this to work."
    elif text.lower() == 'disable':
        set_game_state(db, conn, chan, duck_kick=False)
        return "kicking for non-existent ducks has been disabled."
    else:
        notice_doc()
        return


def generate_duck():
    """Try and randomize the duck message so people can't highlight on it/script against it."""
    duck_tail = random.choice(duck_tails)
    rt = random.randint(1, len(duck_tail) - 1)
    dtail = duck_tail[:rt] + u' \u200b ' + duck_tail[rt:]
    dbody = random.choice(duck)
    rb = random.randint(1, len(dbody) - 1)
    dbody = dbody[:rb] + u'\u200b' + dbody[rb:]
    dnoise = random.choice(duck_noise)
    rn = random.randint(1, len(dnoise) - 1)
    dnoise = dnoise[:rn] + u'\u200b' + dnoise[rn:]
    return dtail, dbody, dnoise


@hook.periodic(11, initial_interval=11)
def deploy_duck(bot):
    global game_status
    for network in game_status:
        if network not in bot.connections:
            continue
        conn = bot.connections[network]
        if not conn.ready:
            continue
        for chan in game_status[network]:
            active = game_status[network][chan]['game_on']
            duck_status = game_status[network][chan]['duck_status']
            next_duck = game_status[network][chan]['next_duck_time']
            chan_messages = game_status[network][chan]['messages']
            chan_masks = game_status[network][chan]['masks']
            if active == 1 and duck_status == 0 and next_duck <= time() and chan_messages >= MSG_DELAY and len(
                chan_masks) >= MASK_REQ:
                # deploy a duck to channel
                game_status[network][chan]['duck_status'] = 1
                game_status[network][chan]['duck_time'] = time()
                dtail, dbody, dnoise = generate_duck()
                conn.message(chan, "{}{}{}".format(dtail, dbody, dnoise))
            continue
        continue


def hit_or_miss(deploy, shoot):
    """This function calculates if the befriend or bang will be successful."""
    if shoot - deploy < 1:
        return .05
    elif 1 <= shoot - deploy <= 7:
        out = random.uniform(.60, .75)
        return out
    else:
        return 1


def dbadd_entry(nick, chan, db, conn, shoot, friend):
    """Takes care of adding a new row to the database."""
    query = table.insert().values(
        network=conn.name,
        chan=chan.lower(),
        name=nick.lower(),
        shot=shoot,
        befriend=friend)
    db.execute(query)
    db.commit()


def dbupdate(nick, chan, db, conn, shoot, friend):
    """update a db row"""
    if shoot and not friend:
        query = table.update() \
            .where(table.c.network == conn.name) \
            .where(table.c.chan == chan.lower()) \
            .where(table.c.name == nick.lower()) \
            .values(shot=shoot)
        db.execute(query)
        db.commit()
    elif friend and not shoot:
        query = table.update() \
            .where(table.c.network == conn.name) \
            .where(table.c.chan == chan.lower()) \
            .where(table.c.name == nick.lower()) \
            .values(befriend=friend)
        db.execute(query)
        db.commit()
    elif friend and shoot:
        query = table.update() \
            .where(table.c.network == conn.name) \
            .where(table.c.chan == chan.lower()) \
            .where(table.c.name == nick.lower()) \
            .values(befriend=friend) \
            .values(shot=shoot)
        db.execute(query)
        db.commit()


def update_score(nick, chan, db, conn, shoot=0, friend=0):
    score = db.execute(select([table.c.shot, table.c.befriend])
                       .where(table.c.network == conn.name)
                       .where(table.c.chan == chan.lower())
                       .where(table.c.name == nick.lower())).fetchone()
    if score:
        dbupdate(nick, chan, db, conn, score[0] + shoot, score[1] + friend)
        return {'shoot': score[0] + shoot, 'friend': score[1] + friend}
    else:
        dbadd_entry(nick, chan, db, conn, shoot, friend)
        return {'shoot': shoot, 'friend': friend}

def get_animal():    
    return random.choice([
        'duck',
        'computer',
    ])

def attack(event, nick, chan, message, db, conn, notice, attack):
    global game_status, scripters
    if chan in opt_out:
        return

    network = conn.name
    status = game_status[network][chan]

    animal = get_animal()

    out = ""
    if attack == "smoke":
        pronoun = random.choice([
            ('He', 'his'),
            ('She', 'her'),
            ('They', 'their'),
            ('Ze', 'hir'),
        ])
        duck_stories = [
            '{} tells you about {} xmonad setup.',
            '{} talks about IceWM.',
            '{} talks about evilwm.',
            '{} discusses gentoo.',
            '{} reminisces about freshmeat.net, and other stuff before github.', 
            '{} discusses arch linux.',
            '{} discusses HaikuOS.',
            '{} discusses OpenBSD.',
            '{} discusses life, the universe, and everything.',
            '{} tells you about {} Amiga.',
            '{} tells you about {} Sun station.',
            '{} wants to play OpenTTD with you.',
            '{} draws a picture of Lennart Poettering.',
        ]
        story = random.choice(duck_stories).format(pronoun[0], pronoun[1])

        smoke_types = [
            "smoke a bud",
            "smoke a bowl",
            "smoke a joint",
            "smoke a spliff",
            "smoke a rollie",
            "smoke a bowl of kief",
            "hit the blunt",
            "eat magic brownies",
            "do knifers",
            "dab",
            "vape",
            "toke up",
            "get blazed",
        ]
        smoke_type = random.choice(smoke_types)

        msg = "{} " + "You {} with the {} ".format(smoke_type, animal) + "for {:.3f} seconds." + " {} You just made a new {} friend. :)".format(story, animal)
        if random.random() > 0.75:
            msg = ' '.join([
                random.choice(WORDS), random.choice(WORDS),
                random.choice(WORDS), random.choice(WORDS),
                random.choice(WORDS), random.choice(WORDS),
                random.choice(WORDS), random.choice(WORDS),
                random.choice(WORDS), random.choice(WORDS),
                random.choice(WORDS), random.choice(WORDS),
                random.choice(WORDS),
            ]) + '.'
        no_duck = "No {}s around to {} with, so you {} with rblor.".format(animal, random.choice(WORDS), smoke_type)
        if random.random() >= 0.5:
            no_duck = ' '.join([
                random.choice(WORDS), random.choice(WORDS),
                random.choice(WORDS), random.choice(WORDS),
                random.choice(WORDS), random.choice(WORDS),
                random.choice(WORDS), random.choice(WORDS),
                random.choice(WORDS), random.choice(WORDS),
                random.choice(WORDS), random.choice(WORDS),
                random.choice(WORDS),
            ]) + '.'
        scripter_msg = "You tried smoking with that " + animal + " in {:.3f} seconds!! Are you sure you aren't a bot? Take a 2 hour cool down."
        attack_type = "friend"
        # TODO: add secret 420 functionality
    elif attack == "shoot":
        miss = [
            "uhhh. You missed the {} completely! Phew! Be nice to {}s. Try being friends next time.".format(animal, animal),
            "Your gun jammed! Be nice to {}s. Try being friends next time.".format(animal),
            "The {} somehow survived your brutal attack. Be nice to {}s. Try being friends next time.".format(animal, animal),
            "The {} is using Linux and they avoid your attack with a crazy firewall.".format(animal),
        ]
        no_duck = random.choice([
            "You shoot in the air and scare away all teh {}s.".format(animal),
            "You shoot in the air and attract some {}s.".format(animal),
            "Damn, you accidentally shot yourself and turn into a {}!".format(animal),
        ])
        msg = "{} you shot the blood in {:.3f} seconds. Your hands are covered in " + animal + "s! .wash them off, weirdo! You've killed {} in {}."
        scripter_msg = "You pulled the trigger in {:.3f} seconds, that's really fast. Are you sure you aren't a bot? Take a 2 hour cool down."
        attack_type = "shoot"
    else:
        miss = [
            "The {} didn't want to be friends, maybe next time.".format(animal),
            "Well this is awkward, the {} needs to think about it. 🤔".format(animal),
            "The {} said no, maybe bribe it with some mota? {}s love mota don't they?".format(animal),
        ]
        no_duck = "You tried befriending a non-existent " + animal + ". Some people would say that's creepy. But you're probably just lonely. Look up some funny youtube videos."
        msg = "{} you made friends a cup of " + animal + " blood and the " + animal + " that it used to belong to in {:.3f} seconds! You've made friends with {} in {}."
        scripter_msg = "You tried friending that " + animal + " in {:.3f} seconds, that's fast as hell. Are you sure you aren't a bot? Take a 2 hour cool down."
        attack_type = "friend"

    if not status['game_on']:
        return None
    elif status['duck_status'] != 1:
        if status['no_duck_kick'] == 1:
            conn.cmd("KICK", chan, nick, no_duck)
            return

        return no_duck
    else:
        status['shoot_time'] = time()
        deploy = status['duck_time']
        shoot = status['shoot_time']
        if nick.lower() in scripters:
            if scripters[nick.lower()] > shoot:
                notice(
                    "You are in a cool down period, you can try again in {:.3f} seconds.".format(
                        scripters[nick.lower()] - shoot
                    )
                )
                return

        chance = hit_or_miss(deploy, shoot)
        if not random.random() <= chance and chance > .05:
            out = random.choice(miss) + " You can try again in 7 seconds."
            scripters[nick.lower()] = shoot + 7
            return out

        if chance == .05:
            out += scripter_msg.format(shoot - deploy)
            scripters[nick.lower()] = shoot + 7200
            if not random.random() <= chance:
                return random.choice(miss) + " " + out
            else:
                message(out)

        status['duck_status'] = 2
        try:
            args = {
                attack_type: 1
            }

            score = update_score(nick, chan, db, conn, **args)[attack_type]
        except Exception:
            status['duck_status'] = 1
            event.reply("An unknown error has occurred.")
            raise

        message(msg.format(nick, shoot - deploy, pluralize_auto(score, "duck"), chan))
        set_ducktime(chan, conn.name)


@hook.command("bang", autohelp=False)
def bang(nick, chan, message, db, conn, notice, event):
    """- when there is a duck on the loose use this command to shoot it."""
    with chan_locks[conn.name][chan.casefold()]:
        return attack(event, nick, chan, message, db, conn, notice, "shoot")


@hook.command("bait", autohelp=False)
def bait(nick, chan, message, db, conn, notice, event):
    """- Throw some duck bait."""
    baits = [
        ('Quality Burritos', '🌯🌯🌯'),
        ('blue zarf', '💊. . .💊'),
        ('vic\'s pizza', '🍕🍕'),
        ('Wendy\'s chicken sandwiches', 'I can\'t believe you wasted those!'),
        ('frozen pierogies from grocery outlet',),
        ('pepsi fries',),
    ]
    with chan_locks[conn.name][chan.casefold()]:
        bait = random.choice(baits)
        msg = 'You throw some {} out to the chan. Let\'s see if any ' + get_animal() + 's take teh bait... {}'.format(
            bait[0], bait[1] if len(bait) > 1 else '')
        return msg


@hook.command("wash", autohelp=False)
def wash(nick, chan, message, db, conn, notice, event):
    """- Wash the duck blood off your hands."""
    with chan_locks[conn.name][chan.casefold()]:
        msg = 'You wash the {}s off your hands, they scamper and float away.'.format(get_animal())
        return msg


@hook.command("smoke", autohelp=False)
def smoke(nick, chan, message, db, conn, notice, event):
    """- Smoke with the duck."""
    with chan_locks[conn.name][chan.casefold()]:
        return attack(event, nick, chan, message, db, conn, notice, "smoke")


@hook.command("befriend", autohelp=False)
def befriend(nick, chan, message, db, conn, notice, event):
    """- when there is a duck on the loose use this command to befriend it before someone else shoots it."""
    with chan_locks[conn.name][chan.casefold()]:
        return attack(event, nick, chan, message, db, conn, notice, "befriend")


def smart_truncate(content, length=320, suffix='...'):
    if len(content) <= length:
        return content
    else:
        return content[:length].rsplit(' • ', 1)[0] + suffix


@hook.command("friends", autohelp=False)
def friends(text, chan, conn, db):
    """[{global|average}] - Prints a list of the top duck friends in the channel, if 'global' is specified all channels in the database are included."""
    if chan in opt_out:
        return
    friends = defaultdict(int)
    chancount = defaultdict(int)
    if text.lower() == 'global' or text.lower() == 'average':
        out = "Duck friend scores across the network: "
        scores = db.execute(select([table.c.name, table.c.befriend]) \
                            .where(table.c.network == conn.name) \
                            .order_by(desc(table.c.befriend)))
        if scores:
            for row in scores:
                if row[1] == 0:
                    continue
                chancount[row[0]] += 1
                friends[row[0]] += row[1]
            if text.lower() == 'average':
                for k, v in friends.items():
                    friends[k] = int(v / chancount[k])
        else:
            return "it appears no on has friended any ducks yet."
    else:
        out = "Duck friend scores in {}: ".format(chan)
        scores = db.execute(select([table.c.name, table.c.befriend]) \
                            .where(table.c.network == conn.name) \
                            .where(table.c.chan == chan.lower()) \
                            .order_by(desc(table.c.befriend)))
        if scores:
            for row in scores:
                if row[1] == 0:
                    continue
                friends[row[0]] += row[1]
        else:
            return "it appears no on has friended any ducks yet."

    topfriends = sorted(friends.items(), key=operator.itemgetter(1), reverse=True)
    out += ' • '.join(["{}: {:,}".format('\x02' + k[:1] + u'\u200b' + k[1:] + '\x02', v) for k, v in topfriends])
    out = smart_truncate(out)
    return out


@hook.command("killers", autohelp=False)
def killers(text, chan, conn, db):
    """[{global|average}] - Prints a list of the top duck killers in the channel, if 'global' is specified all channels in the database are included."""
    if chan in opt_out:
        return
    killers = defaultdict(int)
    chancount = defaultdict(int)
    if text.lower() == 'global' or text.lower() == 'average':
        out = "Duck killer scores across the network: "
        scores = db.execute(select([table.c.name, table.c.shot]) \
                            .where(table.c.network == conn.name) \
                            .order_by(desc(table.c.shot)))
        if scores:
            for row in scores:
                if row[1] == 0:
                    continue
                chancount[row[0]] += 1
                killers[row[0]] += row[1]
            if text.lower() == 'average':
                for k, v in killers.items():
                    killers[k] = int(v / chancount[k])
        else:
            return "it appears no on has killed any ducks yet."
    else:
        out = "Duck killer scores in {}: ".format(chan)
        scores = db.execute(select([table.c.name, table.c.shot]) \
                            .where(table.c.network == conn.name) \
                            .where(table.c.chan == chan.lower()) \
                            .order_by(desc(table.c.shot)))
        if scores:
            for row in scores:
                if row[1] == 0:
                    continue
                killers[row[0]] += row[1]
        else:
            return "it appears no on has killed any ducks yet."

    topkillers = sorted(killers.items(), key=operator.itemgetter(1), reverse=True)
    out += ' • '.join(["{}: {:,}".format('\x02' + k[:1] + u'\u200b' + k[1:] + '\x02', v) for k, v in topkillers])
    out = smart_truncate(out)
    return out


@hook.command("duckforgive", permissions=["op", "ignore"])
def duckforgive(text):
    """<nick> - Allows people to be removed from the mandatory cooldown period."""
    global scripters
    if text.lower() in scripters and scripters[text.lower()] > time():
        scripters[text.lower()] = 0
        return "{} has been removed from the mandatory cooldown period.".format(text)
    else:
        return "I couldn't find anyone banned from the hunt by that nick"


@hook.command("hunt_opt_out", permissions=["op", "ignore"], autohelp=False)
def hunt_opt_out(text, chan, db, conn):
    """[{add <chan>|remove <chan>|list}] - Running this command without any arguments displays the status of the current channel. hunt_opt_out add #channel will disable all duck hunt commands in the specified channel. hunt_opt_out remove #channel will re-enable the game for the specified channel."""
    if not text:
        if chan in opt_out:
            return "Duck hunt is disabled in {}. To re-enable it run .hunt_opt_out remove #channel".format(chan)
        else:
            return "Duck hunt is enabled in {}. To disable it run .hunt_opt_out add #channel".format(chan)
    if text == "list":
        return ", ".join(opt_out)
    if len(text.split(' ')) < 2:
        return "please specify add or remove and a valid channel name"
    command = text.split()[0]
    channel = text.split()[1]
    if not channel.startswith('#'):
        return "Please specify a valid channel."
    if command.lower() == "add":
        if channel in opt_out:
            return "Duck hunt has already been disabled in {}.".format(channel)
        query = optout.insert().values(
            network=conn.name,
            chan=channel.lower())
        db.execute(query)
        db.commit()
        load_optout(db)
        return "The duckhunt has been successfully disabled in {}.".format(channel)
    if command.lower() == "remove":
        if not channel in opt_out:
            return "Duck hunt is already enabled in {}.".format(channel)
        delete = optout.delete(optout.c.chan == channel.lower())
        db.execute(delete)
        db.commit()
        load_optout(db)


@hook.command("duckmerge", permissions=["botcontrol"])
def duck_merge(text, conn, db, message):
    """<user1> <user2> - Moves the duck scores from one nick to another nick. Accepts two nicks as input the first will have their duck scores removed the second will have the first score added. Warning this cannot be undone."""
    oldnick, newnick = text.lower().split()
    if not oldnick or not newnick:
        return "Please specify two nicks for this command."
    oldnickscore = db.execute(select([table.c.name, table.c.chan, table.c.shot, table.c.befriend])
                              .where(table.c.network == conn.name)
                              .where(table.c.name == oldnick)).fetchall()
    newnickscore = db.execute(select([table.c.name, table.c.chan, table.c.shot, table.c.befriend])
                              .where(table.c.network == conn.name)
                              .where(table.c.name == newnick)).fetchall()
    duckmerge = defaultdict(lambda: defaultdict(int))
    duckmerge["TKILLS"] = 0
    duckmerge["TFRIENDS"] = 0
    channelkey = {"update": [], "insert": []}
    if oldnickscore:
        if newnickscore:
            for row in newnickscore:
                duckmerge[row["chan"]]["shot"] = row["shot"]
                duckmerge[row["chan"]]["befriend"] = row["befriend"]
            for row in oldnickscore:
                if row["chan"] in duckmerge:
                    duckmerge[row["chan"]]["shot"] = duckmerge[row["chan"]]["shot"] + row["shot"]
                    duckmerge[row["chan"]]["befriend"] = duckmerge[row["chan"]]["befriend"] + row["befriend"]
                    channelkey["update"].append(row["chan"])
                    duckmerge["TKILLS"] = duckmerge["TKILLS"] + row["shot"]
                    duckmerge["TFRIENDS"] = duckmerge["TFRIENDS"] + row["befriend"]
                else:
                    duckmerge[row["chan"]]["shot"] = row["shot"]
                    duckmerge[row["chan"]]["befriend"] = row["befriend"]
                    channelkey["insert"].append(row["chan"])
                    duckmerge["TKILLS"] = duckmerge["TKILLS"] + row["shot"]
                    duckmerge["TFRIENDS"] = duckmerge["TFRIENDS"] + row["befriend"]
        else:
            for row in oldnickscore:
                duckmerge[row["chan"]]["shot"] = row["shot"]
                duckmerge[row["chan"]]["befriend"] = row["befriend"]
                channelkey["insert"].append(row["chan"])
                # TODO: Call dbupdate() and db_add_entry for the items in duckmerge
        for channel in channelkey["insert"]:
            dbadd_entry(newnick, channel, db, conn, duckmerge[channel]["shot"], duckmerge[channel]["befriend"])
        for channel in channelkey["update"]:
            dbupdate(newnick, channel, db, conn, duckmerge[channel]["shot"], duckmerge[channel]["befriend"])
        query = table.delete() \
            .where(table.c.network == conn.name) \
            .where(table.c.name == oldnick)
        db.execute(query)
        db.commit()
        message("Migrated {} and {} from {} to {}".format(
            pluralize_auto(duckmerge["TKILLS"], "duck kill"), pluralize_auto(duckmerge["TFRIENDS"], "duck friend"),
            oldnick, newnick
        ))
    else:
        return "There are no duck scores to migrate from {}".format(oldnick)


@hook.command("ducks", autohelp=False)
def ducks_user(text, nick, chan, conn, db, message):
    """<nick> - Prints a users duck stats. If no nick is input it will check the calling username."""
    name = nick.lower()
    if text:
        name = text.split()[0].lower()
    ducks = defaultdict(int)
    scores = db.execute(select([table.c.name, table.c.chan, table.c.shot, table.c.befriend])
                        .where(table.c.network == conn.name)
                        .where(table.c.name == name)).fetchall()
    if text:
        name = text.split()[0]
    else:
        name = nick
    if scores:
        has_hunted_in_chan = False
        for row in scores:
            if row["chan"].lower() == chan.lower():
                has_hunted_in_chan = True
                ducks["chankilled"] += row["shot"]
                ducks["chanfriends"] += row["befriend"]
            ducks["killed"] += row["shot"]
            ducks["friend"] += row["befriend"]
            ducks["chans"] += 1
        # Check if the user has only participated in the hunt in this channel
        if ducks["chans"] == 1 and has_hunted_in_chan:
            message("{} has killed {} and befriended {} in {}.".format(
                name, pluralize_auto(ducks["chankilled"], "duck"), pluralize_auto(ducks["chanfriends"], "duck"), chan
            ))
            return
        kill_average = int(ducks["killed"] / ducks["chans"])
        friend_average = int(ducks["friend"] / ducks["chans"])
        message(
            "\x02{}'s\x02 duck stats: \x02{}\x02 killed and \x02{}\x02 befriended in {}. Across {}: \x02{}\x02 killed and \x02{}\x02 befriended. Averaging \x02{}\x02 and \x02{}\x02 per channel.".format(
                name, pluralize_auto(ducks["chankilled"], "duck"), pluralize_auto(ducks["chanfriends"], "duck"),
                chan, pluralize_auto(ducks["chans"], "channel"),
                pluralize_auto(ducks["killed"], "duck"), pluralize_auto(ducks["friend"], "duck"),
                pluralize_auto(kill_average, "kill"), pluralize_auto(friend_average, "friend")
            )
        )
    else:
        return "It appears {} has not participated in the {} hunt.".format(name, get_animal())


@hook.command("duckstats", autohelp=False)
def duck_stats(chan, conn, db, message):
    """- Prints duck statistics for the entire channel and totals for the network."""
    ducks = defaultdict(int)
    scores = db.execute(select([table.c.name, table.c.chan, table.c.shot, table.c.befriend])
                        .where(table.c.network == conn.name)).fetchall()
    if scores:
        ducks["friendchan"] = defaultdict(int)
        ducks["killchan"] = defaultdict(int)
        for row in scores:
            ducks["friendchan"][row["chan"]] += row["befriend"]
            ducks["killchan"][row["chan"]] += row["shot"]
            # ducks["chans"] += 1
            if row["chan"].lower() == chan.lower():
                ducks["chankilled"] += row["shot"]
                ducks["chanfriends"] += row["befriend"]
            ducks["killed"] += row["shot"]
            ducks["friend"] += row["befriend"]
        ducks["chans"] = int((len(ducks["friendchan"]) + len(ducks["killchan"])) / 2)
        killerchan, killscore = sorted(ducks["killchan"].items(), key=operator.itemgetter(1), reverse=True)[0]
        friendchan, friendscore = sorted(ducks["friendchan"].items(), key=operator.itemgetter(1), reverse=True)[0]
        message(
            "\x02Duck Stats:\x02 {:,} killed and {:,} befriended in \x02{}\x02. Across {} \x02{:,}\x02 ducks have been killed and \x02{:,}\x02 befriended. \x02Top Channels:\x02 \x02{}\x02 with {} and \x02{}\x02 with {}".format(
                ducks["chankilled"], ducks["chanfriends"], chan, pluralize_auto(ducks["chans"], "channel"),
                ducks["killed"], ducks["friend"],
                killerchan, pluralize_auto(killscore, "kill"),
                friendchan, pluralize_auto(friendscore, "friend")
            ))
    else:
        return "It looks like there has been no duck activity on this channel or network."
