# Copyright 2011,2012 James McCauley
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

"""
An ARP utility that can learn and proxy ARPs, and can also answer queries
from a list of static entries.

This adds the "arp" object to the console, which you can use to look at
or modify the ARP table.
"""

from pox.core import core
import pox
log = core.getLogger()

from pox.lib.packet.ethernet import ethernet, ETHER_BROADCAST
from pox.lib.packet.arp import arp
from pox.lib.addresses import IPAddr, EthAddr
from pox.lib.util import dpid_to_str, str_to_bool
from pox.lib.recoco import Timer
from pox.lib.revent import EventHalt

import pox.openflow.libopenflow_01 as of

import time


# Timeout for ARP entries
ARP_TIMEOUT = 60 * 4


class Entry (object):
  """
  We use the MAC to answer ARP replies.
  We use the timeout so that if an entry is older than ARP_TIMEOUT, we
   flood the ARP request rather than try to answer it ourselves.
  """
  def __init__ (self, mac, static = False):
    self.timeout = time.time() + ARP_TIMEOUT
    self.static = static
    if mac is True:
      # Means use switch's MAC, implies True
      self.mac = True
      self.static = True
    else:
      self.mac = EthAddr(mac)

  def __eq__ (self, other):
    if isinstance(other, Entry):
      return (self.static,self.mac)==(other.static,other.mac)
    else:
      return self.mac == other
  def __ne__ (self, other):
    return not self.__eq__(other)

  @property
  def is_expired (self):
    if self.static: return False
    return time.time() > self.timeout


class ARPTable (dict):
  def __repr__ (self):
    o = []
    for k,e in self.iteritems():
      t = int(e.timeout - time.time())
      if t < 0:
        t = "X"
      else:
        t = str(t) + "s left"
      if e.static: t = "-"
      mac = e.mac
      if mac is True: mac = "<Switch MAC>"
      o.append((k,"%-17s %-20s %3s" % (k, mac, t)))

    for k,t in _failed_queries.iteritems():
      if k not in self:
        t = int(time.time() - t)
        o.append((k,"%-17s %-20s %3ss ago" % (k, '?', t)))

    o.sort()
    o = [e[1] for e in o]
    o.insert(0,"-- ARP Table -----")
    if len(o) == 1:
      o.append("<< Empty >>")
    return "\n".join(o)

  def __setitem__ (self, key, val):
    key = IPAddr(key)
    if not isinstance(val, Entry):
      val = Entry(val)
    dict.__setitem__(self, key, val)

  def __delitem__ (self, key):
    key = IPAddr(key)
    dict.__delitem__(self, key)

  def set (self, key, value=True, static=True):
    if not isinstance(value, Entry):
      value = Entry(value, static=static)
    self[key] = value


def _dpid_to_mac (dpid):
  # Should maybe look at internal port MAC instead?
  return EthAddr("%012x" % (dpid & 0xffFFffFFffFF,))


def _handle_expiration ():
  for k,e in _arp_table.items():
    if e.is_expired:
      del _arp_table[k]
  for k,t in _failed_queries.items():
    if time.time() - t > ARP_TIMEOUT:
      del _failed_queries[k]


class ARPResponder (object):
  def __init__ (self):
    # This timer handles expiring stuff
    self._expire_timer = Timer(5, _handle_expiration, recurring=True)

    core.addListeners(self)

  def _handle_GoingUpEvent (self, event):
    core.openflow.addListeners(self)
    log.debug("Up...")

  def _handle_ConnectionUp (self, event):
    if _install_flow:
      fm = of.ofp_flow_mod()
      fm.priority = 0x7000 # Pretty high
      fm.match.dl_type = ethernet.ARP_TYPE
      fm.actions.append(of.ofp_action_output(port=of.OFPP_CONTROLLER))
      event.connection.send(fm)

  def _handle_PacketIn (self, event):
    squelch = False

    dpid = event.connection.dpid
    inport = event.port
    packet = event.parsed
    if not packet.parsed:
      log.warning("%s: ignoring unparsed packet", dpid_to_str(dpid))
      return

    a = packet.find('arp')
    if not a: return

    log.debug("%s ARP %s %s => %s", dpid_to_str(dpid),
      {arp.REQUEST:"request",arp.REPLY:"reply"}.get(a.opcode,
      'op:%i' % (a.opcode,)), str(a.protosrc), str(a.protodst))

    if a.prototype == arp.PROTO_TYPE_IP:
      if a.hwtype == arp.HW_TYPE_ETHERNET:
        if a.protosrc != 0:

          if _learn:
            # Learn or update port/MAC info
            if a.protosrc in _arp_table:
              if _arp_table[a.protosrc] != packet.src:
                log.warn("%s RE-learned %s", dpid_to_str(dpid), a.protosrc)
            else:
              log.info("%s learned %s", dpid_to_str(dpid), a.protosrc)
            _arp_table[a.protosrc] = Entry(packet.src)

          if a.opcode == arp.REQUEST:
            # Maybe we can answer

            if a.protodst in _arp_table:
              # We have an answer...

              r = arp()
              r.hwtype = a.hwtype
              r.prototype = a.prototype
              r.hwlen = a.hwlen
              r.protolen = a.protolen
              r.opcode = arp.REPLY
              r.hwdst = a.hwsrc
              r.protodst = a.protosrc
              r.protosrc = a.protodst
              mac = _arp_table[a.protodst].mac
              if mac is True:
                # Special case -- use ourself
                mac = _dpid_to_mac(dpid)
              r.hwsrc = mac
              e = ethernet(type=packet.type, src=_dpid_to_mac(dpid),
                            dst=a.hwsrc)
              e.payload = r
              log.info("%s answering ARP for %s" % (dpid_to_str(dpid),
                str(r.protosrc)))
              msg = of.ofp_packet_out()
              msg.data = e.pack()
              msg.actions.append(of.ofp_action_output(port =
                                                      of.OFPP_IN_PORT))
              msg.in_port = inport
              event.connection.send(msg)
              return EventHalt if _eat_packets else None
            else:
              # Keep track of failed queries
              squelch = a.protodst in _failed_queries
              _failed_queries[a.protodst] = time.time()

    # Didn't know how to handle this ARP, so just flood it
    msg = "%s flooding ARP %s %s => %s" % (dpid_to_str(dpid),
        {arp.REQUEST:"request",arp.REPLY:"reply"}.get(a.opcode,
        'op:%i' % (a.opcode,)), a.protosrc, a.protodst)

    if squelch:
      log.debug(msg)
    else:
      log.info(msg)

    msg = of.ofp_packet_out()
    msg.actions.append(of.ofp_action_output(port = of.OFPP_FLOOD))
    msg.data = event.ofp
    event.connection.send(msg.pack())
    return EventHalt if _eat_packets else None


_arp_table = ARPTable() # IPAddr -> Entry
_install_flow = None
_eat_packets = None
_failed_queries = {} # IP -> time : queries we couldn't answer
_learn = None

def launch (timeout=ARP_TIMEOUT, no_flow=False, eat_packets=True,
            no_learn=False, **kw):
  global ARP_TIMEOUT, _install_flow, _eat_packets, _learn
  ARP_TIMEOUT = timeout
  _install_flow = not no_flow
  _eat_packets = str_to_bool(eat_packets)
  _learn = not no_learn

  core.Interactive.variables['arp'] = _arp_table
  for k,v in kw.iteritems():
    _arp_table[IPAddr(k)] = Entry(v, static=True)
  core.registerNew(ARPResponder)
