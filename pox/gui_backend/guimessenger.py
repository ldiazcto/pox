# Copyright 2011 James McCauley
# Copyright 2011 Kyriakos Zarifis
#
# This file is part of POX.
#
# POX is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# POX is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with POX.  If not, see <http://www.gnu.org/licenses/>.

from pox.core import core as core
from pox.messenger.messenger import MessageReceived
#import weakref
import json

log = core.getLogger()


from pox.core import core
from pox.messenger.messenger import *
from pox.lib.revent import *
import traceback
"""
from pox.messenger.log_service import LogMessenger
"""
log = core.getLogger()

class GuiMessengerService (EventMixin):
  _wantComponents = set(['topology', 'openflow_discovery'])
  def __init__ (self, connection, params):
    core.listenToDependencies(self, self._wantComponents)
    self.connection = connection
    """
    connection._newlines = params.get("newlines", True) == True #HACK
    
    # Make LogMessenger always send back "source":"logger"
    params['opaque'] = {'type':'log'}
    self._logService = LogMessenger(connection, params) # Aggregate
    # Unhook its message received listener (we will pass it those events
    # manually ourselves...)
    connection.removeListener(dict(self._logService._listeners)[MessageReceived])
    """
    self.listenTo(connection)
    
  def _handle_topology_SwitchJoin(self, event):
    msg = {}
    msg["type"] = "topology"
    msg["command"] = "add"
    msg["node_id"] = [str(event.switch.id)]
    msg["node_type"] = "switch"
    self.connection.send(msg)
    
  def _handle_topology_HostJoin(self, event):
    msg = {}
    msg["type"] = "topology"
    msg["command"] = "add"
    msg["node_id"] = [str(event.host.id)]
    msg["node_type"] = "host"
    self.connection.send(msg)
    
  def _handle_openflow_discovery_LinkEvent (self, event):
    msg = {}
    msg["type"] = "topology"
    msg["command"] = "add"
    msg["links"] = [{"src id":str(event.link.dpid1), "src port":event.link.port1,\
                    "dst id":str(event.link.dpid2), "dst port":event.link.port2,\
                    "src type":None, "dst type":None}]
    msg["node_type"] = "host"
    self.connection.send(msg)

  def _handle_MessageReceived (self, event, msg):
    if event.con.isReadable():
      r = event.con.read()
      if type(r) is dict:
        if "bye" in r:
          event.con.close()
        else:
          if "type" in r:
            # Dispatch message
            if r["type"] == "topology":
              pass
            elif r["type"] == "monitoring":
              pass
            elif r["type"] == "spanning_tree":
              pass
            elif r["type"] == "sample_routing":
              pass
            elif r["type"] == "flowtracer":
              pass
            elif r["type"] == "log":
              pass
              #self._logService._processParameters(r)
            else:
              log.warn("Unknown type for message: %s", r)
          else:
            log.warn("Missing type for message: %s", r)
 

class GuiMessengerServiceListener (object):
  def __init__ (self):
    core.messenger.addListener(MessageReceived, self._handle_global_MessageReceived)

  def _handle_global_MessageReceived (self, event, msg):
    try:
      if msg['hello'] == 'gui':
        # It's for me!
        try:
          GuiMessengerService(event.con, msg)
          event.claim()
          return True
        except:
          traceback.print_exc()
    except:
      pass


def launch ():
  def realStart (event=None):
    if not core.hasComponent("messenger"):
      if event is None:
        # Only do this the first time
        log.warning("Deferring firing up GuiMessengerServiceListener because Messenger isn't up yet")
        core.addListenerByName("ComponentRegistered", realStart, once=True)
      return
    if not core.hasComponent(GuiMessengerServiceListener.__name__):
      core.registerNew(GuiMessengerServiceListener)
      log.info("Up...")

  realStart()
