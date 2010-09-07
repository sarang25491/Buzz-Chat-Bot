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

from google.appengine.api import users
from google.appengine.ext import db
from google.appengine.ext import webapp
from google.appengine.ext.webapp import template
from google.appengine.ext.webapp.util import login_required
from google.appengine.ext.webapp.util import run_wsgi_app
from google.appengine.api import users

import buzz_gae_client
import logging
import os
import settings
import xmpp
import pshb


class UserToken(db.Model):
# The user_id is the key_name so we don't have to make it an explicit property
  request_token_string = db.StringProperty()
  access_token_string = db.StringProperty()
  email_address = db.StringProperty()

  def get_request_token(self):
    "Returns request token as a dictionary of tokens including oauth_token, oauth_token_secret and oauth_callback_confirmed."
    return eval(self.request_token_string)

  def set_access_token(self, access_token):
    access_token_string = repr(access_token)
    self.access_token_string = access_token_string

  def get_access_token(self):
    "Returns access token as a dictionary of tokens including consumer_key, consumer_secret, oauth_token and oauth_token_secret"
    return eval(self.access_token_string)

  @staticmethod
  def create_user_token(request_token):
    user = users.get_current_user()
    user_id = user.user_id()
    request_token_string = repr(request_token)

    # TODO(ade) Support users who sign in to AppEngine with a federated identity aka OpenId
    email = user.email()

    logging.info('Creating user token: key_name: %s request_token_string: %s email_address: %s' % (
    user_id, request_token_string, email))

    return UserToken(key_name=user_id, request_token_string=request_token_string, access_token_string='',
                     email_address=email)

  @staticmethod
  def get_current_user_token():
    user = users.get_current_user()
    user_token = UserToken.get_by_key_name(user.user_id())
    return user_token

  @staticmethod
  def access_token_exists():
    user = users.get_current_user()
    user_token = UserToken.get_by_key_name(user.user_id())
    logging.info('user_token: %s' % user_token)
    return user_token and user_token.access_token_string


class WelcomeHandler(webapp.RequestHandler):
  @login_required
  def get(self):
    logging.info("Request body %s" % self.request.body)
    template_values = {}
    if UserToken.access_token_exists():
      template_values['access_token_exists'] = 'true'
    else:
    # Generate the request token
      client = buzz_gae_client.BuzzGaeClient(settings.CONSUMER_KEY, settings.CONSUMER_SECRET)
      request_token = client.get_request_token(settings.CALLBACK_URL)

      # Create the request token and associate it with the current user
      user_token = UserToken.create_user_token(request_token)
      UserToken.put(user_token)

      authorisation_url = client.generate_authorisation_url(request_token)
      logging.info('Authorisation URL is: %s' % authorisation_url)
      template_values['destination'] = authorisation_url

    path = os.path.join(os.path.dirname(__file__), 'welcome.html')
    self.response.out.write(template.render(path, template_values))


class TokenDeletionHandler(webapp.RequestHandler):
  def post(self):
    user_token = UserToken.get_current_user_token()
    UserToken.delete(user_token)
    self.redirect('/')


class DanceFinishingHandler(webapp.RequestHandler):
  def get(self):
    logging.info("Request body %s" % self.request.body)
    oauth_verifier = self.request.get('oauth_verifier')

    client = buzz_gae_client.BuzzGaeClient(settings.CONSUMER_KEY, settings.CONSUMER_SECRET)
    user_token = UserToken.get_current_user_token()
    request_token = user_token.get_request_token()
    access_token = client.upgrade_to_access_token(request_token, oauth_verifier)
    logging.info('SUCCESS: Received access token.')

    user_token.set_access_token(access_token)
    UserToken.put(user_token)
    logging.debug('Access token was: %s' % user_token.access_token_string)

    self.redirect('/profile')


class ProfileViewingHandler(webapp.RequestHandler):
  @login_required
  def get(self):
    user_token = UserToken.get_current_user_token()
    client = buzz_gae_client.BuzzGaeClient(settings.CONSUMER_KEY, settings.CONSUMER_SECRET)
    api_client = client.build_api_client(user_token.get_access_token())
    user_profile_data = api_client.people().get(userId='@me')

    template_values = {}
    template_values['user_profile_data'] = user_profile_data
    template_values['access_token'] = user_token.access_token_string
    path = os.path.join(os.path.dirname(__file__), 'profile.html')
    self.response.out.write(template.render(path, template_values))


class PostsHandler(webapp.RequestHandler):
  def _get_subscription(self):
    logging.info("Headers were: %s" % str(self.request.headers))
    logging.info('Request: %s' % str(self.request))
    id = self.request.get('id')
    subscription = xmpp.Subscription.get_by_id(int(id))
    return subscription

  def get(self):
    """Show all the resources in this collection"""
    logging.info("Headers were: %s" % str(self.request.headers))
    logging.info('Request: %s' % str(self.request))
    logging.info("New content: %s" % self.request.body)
    id = self.request.get('id')

    # If this is a hub challenge
    if self.request.get('hub.challenge'):
    # If this subscription exists
      if self.request.get('hub.mode') == "subscribe" and xmpp.Subscription.get_by_id(int(id)):
        self.response.out.write(self.request.get('hub.challenge'))
        logging.info("Successfully accepted challenge for feed: %s" % self.request.get('hub.topic'))
      else:
        self.response.set_status(404)
        self.response.out.write("Challenge failed")
        logging.info("Challenge failed for feed: %s" % self.request.get('hub.topic'))
      # Once a challenge has been issued there's no point in returning anything other than challenge passed or failed
      return

  def post(self):
    """Create a new resource in this collection"""
    logging.info("Headers were: %s" % str(self.request.headers))
    logging.info('Request: %s' % str(self.request))
    logging.info("New content: %s" % self.request.body)
    id = self.request.get('id')

    subscription = xmpp.Subscription.get_by_id(int(id))
    subscriber = subscription.subscriber
    search_term = subscription.search_term
    parser = pshb.ContentParser(self.request.body, settings.DEFAULT_HUB, settings.ALWAYS_USE_DEFAULT_HUB)
    url = parser.extractFeedUrl()

    if not parser.dataValid():
      parser.logErrors()
      self.response.out.write("Bad entries: %s" % parser.data)
      return
    else:
      posts = parser.extractPosts()
      logging.info("Successfully received %s posts for subscription: %s" % (len(posts), url))
      xmpp.send_posts(posts, subscriber, search_term)
      self.response.set_status(200)

application = webapp.WSGIApplication([
                                         ('/', WelcomeHandler),
                                         ('/delete_tokens', TokenDeletionHandler),
                                         ('/finish_dance', DanceFinishingHandler),
                                         ('/profile', ProfileViewingHandler),
                                         ('/posts', PostsHandler),
                                         ('/_ah/xmpp/message/chat/', xmpp.XmppHandler), ],
                                     debug=True)

def main():
  run_wsgi_app(application)

if __name__ == '__main__':
  main()
