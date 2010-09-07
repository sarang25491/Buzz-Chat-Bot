# Copyright (C) 2010 Google Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

from django.utils import html
from google.appengine.api import xmpp
from google.appengine.ext import db
from google.appengine.ext.webapp import xmpp_handlers

import logging
import urllib
import pshb
import settings

class Subscription(db.Model):
  url = db.StringProperty(required=True)
  search_term = db.StringProperty(required=True)
  subscriber = db.StringProperty()

  def id(self):
    return self.key().id()

  def __eq__(self, other):
    if not other:
      return False
    return self.id() == other.id()

  @staticmethod
  def exists(id):
    """Return True or False to indicate if a subscription with the given id exists"""
    if not id:
      return False
    return Subscription.get_by_id(int(id)) != None

class Tracker(object):
  def __init__(self, hub_subscriber=pshb.HubSubscriber()):
    self.hub_subscriber =  hub_subscriber

  def _valid_subscription(self, message_body):
    return self._extract_search_term(message_body) != ''

  def _extract_search_term(self, message_body):
    search_term = message_body[len('/track'):]
    return search_term.strip()

  def _extract_sender_email_address(self, message_sender):
    return message_sender.split('/')[0]

  def _subscribe(self, message_sender, message_body):
    message_sender = self._extract_sender_email_address(message_sender)
    search_term = self._extract_search_term(message_body)
    url = self._build_subscription_url(search_term)
    logging.info('Subscribing to: %s for user: %s' % (url, message_sender))

    subscription = Subscription(url=url, search_term=search_term, subscriber=message_sender)
    db.put(subscription)

    callback_url = self._build_callback_url(subscription)

    logging.info('Callback URL was: %s' % callback_url)
    self.hub_subscriber.subscribe(url, 'http://pubsubhubbub.appspot.com/', callback_url)

    return subscription

  def _build_callback_url(self, subscription):
    return "http://%s.appspot.com/posts?id=%s" % (settings.APP_NAME, subscription.id())

  def _build_subscription_url(self, search_term):
    search_term = urllib.quote(search_term)
    return 'https://www.googleapis.com/buzz/v1/activities/track?q=%s' % search_term

  def track(self, message_sender, message_body):
    if self._valid_subscription(message_body):
      return self._subscribe(message_sender, message_body)
    else:
      return None

  def _is_number(self, id):
    if not id:
      return False
    id = id.strip()
    for char in id:
      if not char.isdigit():
        return False
    return True

  def untrack(self, message_sender, message_body):
    logging.info('Message is: %s' % message_body)
    id = message_body[len('/untrack'):]
    if not self._is_number(id):
      return None

    id = id.strip()
    subscription = Subscription.get_by_id(int(id))
    logging.info('Subscripton: %s' % str(subscription))
    if not subscription:
      return None

    if subscription.subscriber != self._extract_sender_email_address(message_sender):
      return None
    subscription.delete()

    callback_url = self._build_callback_url(subscription)
    logging.info('Callback URL was: %s' % callback_url)
    self.hub_subscriber.unsubscribe(subscription.url, 'http://pubsubhubbub.appspot.com/', callback_url)
    return subscription


commands = [
  '/help Prints out this message\n',
  '/track [search term] Starts tracking the given search term and returns the id for your subscription\n'
  '/untrack [id] Removes your subscription for that id\n'
]

class MessageBuilder(object):
  def __init__(self):
    self.lines = []

  def _build_raw_message(self, text):
    message = '''<html xmlns='http://jabber.org/protocol/xhtml-im'>
    <body xmlns="http://www.w3.org/1999/xhtml">
    %s
    </body>
    </html>''' % text
    message = message.strip()
    message = message.encode('ascii', 'xmlcharrefreplace')
    return message

  def build_message(self):
    text = ''
    for index,line in enumerate(self.lines):

      text += html.escape(line)
      if (index+1) !=len(self.lines):
        text += '<br></br>'

    return self._build_raw_message(text)

  def add(self, line):
    self.lines.append(line)

  def build_message_from_post(self, post, search_term):
    text = '''%s matched: <a href='%s'>%s</a>''' % (search_term, post.url, post.title)
    return self._build_raw_message(text)

class XmppHandler(xmpp_handlers.CommandHandler):
  def _reply(self, message_builder, message):
    message_to_send = message_builder.build_message()
    logging.info('Message that will be sent: %s' % message_to_send)
    message.reply(message_to_send, raw_xml=True)

  def help_command(self, message=None):
    logging.info('Received message from: %s' % message.sender)

    lines = ['We all need a little help sometimes']
    lines.extend(commands)

    message_builder = MessageBuilder()
    for line in lines:
      message_builder.add(line)
    self._reply(message_builder, message)

  def track_command(self, message=None):
    logging.info('Received message from: %s' % message.sender)

    tracker = Tracker()
    subscription = tracker.track(message.sender, message.body)
    message_builder = MessageBuilder()
    if subscription:
      message_builder.add('Tracking: %s with id: %s' % (subscription.search_term, subscription.id()))
    else:
      message_builder.add('Sorry there was a problem with your last track command <%s>' % message.body)
    self._reply(message_builder, message)

  def untrack_command(self, message=None):
    logging.info('Received message from: %s' % message.sender)

    tracker = Tracker()
    subscription = tracker.untrack(message.sender, message.body)
    message_builder = MessageBuilder()
    if subscription:
      message_builder.add('No longer tracking: %s with id: %s' % (subscription.search_term, subscription.id()))
    else:
      message_builder.add('Untrack failed. That subscription does not exist for you')
    self._reply(message_builder, message)

def send_posts(posts, subscriber, search_term):
  message_builder = MessageBuilder()
  for post in posts:
    xmpp.send_message(subscriber, message_builder.build_message_from_post(post, search_term), raw_xml=True)