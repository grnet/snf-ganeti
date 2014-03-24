#
#

# Copyright (C) 2006, 2007, 2008, 2009, 2010, 2011, 2012, 2013 Google Inc.
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful, but
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the GNU
# General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA
# 02110-1301, USA.


"""Utility function mainly, but not only used by instance LU's."""

import logging
import os

from ganeti import constants
from ganeti import errors
from ganeti import locking
from ganeti import network
from ganeti import objects
from ganeti import pathutils
from ganeti import utils
from ganeti.cmdlib.common import AnnotateDiskParams, \
  ComputeIPolicyInstanceViolation


def BuildInstanceHookEnv(name, primary_node, secondary_nodes, os_type, status,
                         minmem, maxmem, vcpus, nics, disk_template, disks,
                         bep, hvp, hypervisor_name, tags):
  """Builds instance related env variables for hooks

  This builds the hook environment from individual variables.

  @type name: string
  @param name: the name of the instance
  @type primary_node: string
  @param primary_node: the name of the instance's primary node
  @type secondary_nodes: list
  @param secondary_nodes: list of secondary nodes as strings
  @type os_type: string
  @param os_type: the name of the instance's OS
  @type status: string
  @param status: the desired status of the instance
  @type minmem: string
  @param minmem: the minimum memory size of the instance
  @type maxmem: string
  @param maxmem: the maximum memory size of the instance
  @type vcpus: string
  @param vcpus: the count of VCPUs the instance has
  @type nics: list
  @param nics: list of tuples (name, uuid, ip, mac, mode, link, net, netinfo)
      representing the NICs the instance has
  @type disk_template: string
  @param disk_template: the disk template of the instance
  @type disks: list
  @param disks: list of tuples (name, uuid, size, mode)
  @type bep: dict
  @param bep: the backend parameters for the instance
  @type hvp: dict
  @param hvp: the hypervisor parameters for the instance
  @type hypervisor_name: string
  @param hypervisor_name: the hypervisor for the instance
  @type tags: list
  @param tags: list of instance tags as strings
  @rtype: dict
  @return: the hook environment for this instance

  """
  env = {
    "OP_TARGET": name,
    "INSTANCE_NAME": name,
    "INSTANCE_PRIMARY": primary_node,
    "INSTANCE_SECONDARIES": " ".join(secondary_nodes),
    "INSTANCE_OS_TYPE": os_type,
    "INSTANCE_STATUS": status,
    "INSTANCE_MINMEM": minmem,
    "INSTANCE_MAXMEM": maxmem,
    # TODO(2.9) remove deprecated "memory" value
    "INSTANCE_MEMORY": maxmem,
    "INSTANCE_VCPUS": vcpus,
    "INSTANCE_DISK_TEMPLATE": disk_template,
    "INSTANCE_HYPERVISOR": hypervisor_name,
    }
  if nics:
    nic_count = len(nics)
    for idx, (name, uuid, ip, mac, mode, link, net, netinfo) in enumerate(nics):
      if ip is None:
        ip = ""
      if name:
        env["INSTANCE_NIC%d_NAME" % idx] = name
      env["INSTANCE_NIC%d_UUID" % idx] = uuid
      env["INSTANCE_NIC%d_IP" % idx] = ip
      env["INSTANCE_NIC%d_MAC" % idx] = mac
      env["INSTANCE_NIC%d_MODE" % idx] = mode
      env["INSTANCE_NIC%d_LINK" % idx] = link
      if netinfo:
        nobj = objects.Network.FromDict(netinfo)
        env.update(nobj.HooksDict("INSTANCE_NIC%d_" % idx))
      elif network:
        # FIXME: broken network reference: the instance NIC specifies a
        # network, but the relevant network entry was not in the config. This
        # should be made impossible.
        env["INSTANCE_NIC%d_NETWORK_NAME" % idx] = net
      if mode == constants.NIC_MODE_BRIDGED:
        env["INSTANCE_NIC%d_BRIDGE" % idx] = link
  else:
    nic_count = 0

  env["INSTANCE_NIC_COUNT"] = nic_count

  if disks:
    disk_count = len(disks)
    for idx, (name, uuid, size, mode, info) in enumerate(disks):
      if name:
        env["INSTANCE_DISK%d_NAME" % idx] = name
      env["INSTANCE_DISK%d_UUID" % idx] = uuid
      env["INSTANCE_DISK%d_SIZE" % idx] = size
      env["INSTANCE_DISK%d_MODE" % idx] = mode
      env.update(info)
  else:
    disk_count = 0

  env["INSTANCE_DISK_COUNT"] = disk_count

  if not tags:
    tags = []

  env["INSTANCE_TAGS"] = " ".join(tags)

  for source, kind in [(bep, "BE"), (hvp, "HV")]:
    for key, value in source.items():
      env["INSTANCE_%s_%s" % (kind, key)] = value

  return env


def BuildInstanceHookEnvByObject(lu, instance, override=None):
  """Builds instance related env variables for hooks from an object.

  @type lu: L{LogicalUnit}
  @param lu: the logical unit on whose behalf we execute
  @type instance: L{objects.Instance}
  @param instance: the instance for which we should build the
      environment
  @type override: dict
  @param override: dictionary with key/values that will override
      our values
  @rtype: dict
  @return: the hook environment dictionary

  """
  cluster = lu.cfg.GetClusterInfo()
  bep = cluster.FillBE(instance)
  hvp = cluster.FillHV(instance)
  args = {
    "name": instance.name,
    "primary_node": instance.primary_node,
    "secondary_nodes": instance.secondary_nodes,
    "os_type": instance.os,
    "status": instance.admin_state,
    "maxmem": bep[constants.BE_MAXMEM],
    "minmem": bep[constants.BE_MINMEM],
    "vcpus": bep[constants.BE_VCPUS],
    "nics": NICListToTuple(lu, instance.nics),
    "disk_template": instance.disk_template,
    "disks": [(disk.name, disk.uuid, disk.size, disk.mode,
               BuildDiskLogicalIDEnv(instance.disk_template, idx, disk))
              for idx, disk in enumerate(instance.disks)],
    "bep": bep,
    "hvp": hvp,
    "hypervisor_name": instance.hypervisor,
    "tags": instance.tags,
  }
  if override:
    args.update(override)
  return BuildInstanceHookEnv(**args) # pylint: disable=W0142


def GetClusterDomainSecret():
  """Reads the cluster domain secret.

  """
  return utils.ReadOneLineFile(pathutils.CLUSTER_DOMAIN_SECRET_FILE,
                               strict=True)


def CheckNodeNotDrained(lu, node):
  """Ensure that a given node is not drained.

  @param lu: the LU on behalf of which we make the check
  @param node: the node to check
  @raise errors.OpPrereqError: if the node is drained

  """
  if lu.cfg.GetNodeInfo(node).drained:
    raise errors.OpPrereqError("Can't use drained node %s" % node,
                               errors.ECODE_STATE)


def CheckNodeVmCapable(lu, node):
  """Ensure that a given node is vm capable.

  @param lu: the LU on behalf of which we make the check
  @param node: the node to check
  @raise errors.OpPrereqError: if the node is not vm capable

  """
  if not lu.cfg.GetNodeInfo(node).vm_capable:
    raise errors.OpPrereqError("Can't use non-vm_capable node %s" % node,
                               errors.ECODE_STATE)


def RemoveInstance(lu, feedback_fn, instance, ignore_failures):
  """Utility function to remove an instance.

  """
  logging.info("Removing block devices for instance %s", instance.name)

  if not RemoveDisks(lu, instance, ignore_failures=ignore_failures):
    if not ignore_failures:
      raise errors.OpExecError("Can't remove instance's disks")
    feedback_fn("Warning: can't remove instance's disks")

  logging.info("Removing instance %s out of cluster config", instance.name)

  lu.cfg.RemoveInstance(instance.name)

  assert not lu.remove_locks.get(locking.LEVEL_INSTANCE), \
    "Instance lock removal conflict"

  # Remove lock for the instance
  lu.remove_locks[locking.LEVEL_INSTANCE] = instance.name


def RemoveDisks(lu, instance, target_node=None, ignore_failures=False):
  """Remove all disks for an instance.

  This abstracts away some work from `AddInstance()` and
  `RemoveInstance()`. Note that in case some of the devices couldn't
  be removed, the removal will continue with the other ones.

  @type lu: L{LogicalUnit}
  @param lu: the logical unit on whose behalf we execute
  @type instance: L{objects.Instance}
  @param instance: the instance whose disks we should remove
  @type target_node: string
  @param target_node: used to override the node on which to remove the disks
  @rtype: boolean
  @return: the success of the removal

  """
  logging.info("Removing block devices for instance %s", instance.name)

  all_result = True
  ports_to_release = set()
  anno_disks = AnnotateDiskParams(instance, instance.disks, lu.cfg)
  for (idx, device) in enumerate(anno_disks):
    if target_node:
      edata = [(target_node, device)]
    else:
      edata = device.ComputeNodeTree(instance.primary_node)
    for node, disk in edata:
      if lu.op.keep_disks and disk.dev_type in constants.DT_EXT:
        continue
      lu.cfg.SetDiskID(disk, node)
      result = lu.rpc.call_blockdev_remove(node, disk)
      if result.fail_msg:
        lu.LogWarning("Could not remove disk %s on node %s,"
                      " continuing anyway: %s", idx, node, result.fail_msg)
        if not (result.offline and node != instance.primary_node):
          all_result = False

    # if this is a DRBD disk, return its port to the pool
    if device.dev_type in constants.LDS_DRBD:
      ports_to_release.add(device.logical_id[2])

  if all_result or ignore_failures:
    for port in ports_to_release:
      lu.cfg.AddTcpUdpPort(port)

  if instance.disk_template in constants.DTS_FILEBASED:
    if len(instance.disks) > 0:
      file_storage_dir = os.path.dirname(instance.disks[0].logical_id[1])
    else:
      if instance.disk_template == constants.DT_SHARED_FILE:
        file_storage_dir = utils.PathJoin(lu.cfg.GetSharedFileStorageDir(),
                                          instance.name)
      else:
        file_storage_dir = utils.PathJoin(lu.cfg.GetFileStorageDir(),
                                          instance.name)
    if target_node:
      tgt = target_node
    else:
      tgt = instance.primary_node
    result = lu.rpc.call_file_storage_dir_remove(tgt, file_storage_dir)
    if result.fail_msg:
      lu.LogWarning("Could not remove directory '%s' on node %s: %s",
                    file_storage_dir, instance.primary_node, result.fail_msg)
      all_result = False

  return all_result


def NICToTuple(lu, nic):
  """Build a tupple of nic information.

  @type lu:  L{LogicalUnit}
  @param lu: the logical unit on whose behalf we execute
  @type nic: L{objects.NIC}
  @param nic: nic to convert to hooks tuple

  """
  cluster = lu.cfg.GetClusterInfo()
  filled_params = cluster.SimpleFillNIC(nic.nicparams)
  mode = filled_params[constants.NIC_MODE]
  link = filled_params[constants.NIC_LINK]
  netinfo = None
  if nic.network:
    nobj = lu.cfg.GetNetwork(nic.network)
    netinfo = objects.Network.ToDict(nobj)
  return (nic.name, nic.uuid, nic.ip, nic.mac, mode, link, nic.network, netinfo)


def NICListToTuple(lu, nics):
  """Build a list of nic information tuples.

  This list is suitable to be passed to _BuildInstanceHookEnv or as a return
  value in LUInstanceQueryData.

  @type lu:  L{LogicalUnit}
  @param lu: the logical unit on whose behalf we execute
  @type nics: list of L{objects.NIC}
  @param nics: list of nics to convert to hooks tuples

  """
  hooks_nics = []
  for nic in nics:
    hooks_nics.append(NICToTuple(lu, nic))
  return hooks_nics


def CopyLockList(names):
  """Makes a copy of a list of lock names.

  Handles L{locking.ALL_SET} correctly.

  """
  if names == locking.ALL_SET:
    return locking.ALL_SET
  else:
    return names[:]


def ReleaseLocks(lu, level, names=None, keep=None):
  """Releases locks owned by an LU.

  @type lu: L{LogicalUnit}
  @param level: Lock level
  @type names: list or None
  @param names: Names of locks to release
  @type keep: list or None
  @param keep: Names of locks to retain

  """
  assert not (keep is not None and names is not None), \
         "Only one of the 'names' and the 'keep' parameters can be given"

  if names is not None:
    should_release = names.__contains__
  elif keep:
    should_release = lambda name: name not in keep
  else:
    should_release = None

  owned = lu.owned_locks(level)
  if not owned:
    # Not owning any lock at this level, do nothing
    pass

  elif should_release:
    retain = []
    release = []

    # Determine which locks to release
    for name in owned:
      if should_release(name):
        release.append(name)
      else:
        retain.append(name)

    assert len(lu.owned_locks(level)) == (len(retain) + len(release))

    # Release just some locks
    lu.glm.release(level, names=release)

    assert frozenset(lu.owned_locks(level)) == frozenset(retain)
  else:
    # Release everything
    lu.glm.release(level)

    assert not lu.glm.is_owned(level), "No locks should be owned"


def _ComputeIPolicyNodeViolation(ipolicy, instance, current_group,
                                 target_group, cfg,
                                 _compute_fn=ComputeIPolicyInstanceViolation):
  """Compute if instance meets the specs of the new target group.

  @param ipolicy: The ipolicy to verify
  @param instance: The instance object to verify
  @param current_group: The current group of the instance
  @param target_group: The new group of the instance
  @type cfg: L{config.ConfigWriter}
  @param cfg: Cluster configuration
  @param _compute_fn: The function to verify ipolicy (unittest only)
  @see: L{ganeti.cmdlib.common.ComputeIPolicySpecViolation}

  """
  if current_group == target_group:
    return []
  else:
    return _compute_fn(ipolicy, instance, cfg)


def CheckTargetNodeIPolicy(lu, ipolicy, instance, node, cfg, ignore=False,
                           _compute_fn=_ComputeIPolicyNodeViolation):
  """Checks that the target node is correct in terms of instance policy.

  @param ipolicy: The ipolicy to verify
  @param instance: The instance object to verify
  @param node: The new node to relocate
  @type cfg: L{config.ConfigWriter}
  @param cfg: Cluster configuration
  @param ignore: Ignore violations of the ipolicy
  @param _compute_fn: The function to verify ipolicy (unittest only)
  @see: L{ganeti.cmdlib.common.ComputeIPolicySpecViolation}

  """
  primary_node = lu.cfg.GetNodeInfo(instance.primary_node)
  res = _compute_fn(ipolicy, instance, primary_node.group, node.group, cfg)

  if res:
    msg = ("Instance does not meet target node group's (%s) instance"
           " policy: %s") % (node.group, utils.CommaJoin(res))
    if ignore:
      lu.LogWarning(msg)
    else:
      raise errors.OpPrereqError(msg, errors.ECODE_INVAL)


def GetInstanceInfoText(instance):
  """Compute that text that should be added to the disk's metadata.

  """
  return "originstname+%s" % instance.name


def CheckNodeFreeMemory(lu, node, reason, requested, hypervisor_name):
  """Checks if a node has enough free memory.

  This function checks if a given node has the needed amount of free
  memory. In case the node has less memory or we cannot get the
  information from the node, this function raises an OpPrereqError
  exception.

  @type lu: C{LogicalUnit}
  @param lu: a logical unit from which we get configuration data
  @type node: C{str}
  @param node: the node to check
  @type reason: C{str}
  @param reason: string to use in the error message
  @type requested: C{int}
  @param requested: the amount of memory in MiB to check for
  @type hypervisor_name: C{str}
  @param hypervisor_name: the hypervisor to ask for memory stats
  @rtype: integer
  @return: node current free memory
  @raise errors.OpPrereqError: if the node doesn't have enough memory, or
      we cannot check the node

  """
  nodeinfo = lu.rpc.call_node_info([node], None, [hypervisor_name], False)
  nodeinfo[node].Raise("Can't get data from node %s" % node,
                       prereq=True, ecode=errors.ECODE_ENVIRON)
  (_, _, (hv_info, )) = nodeinfo[node].payload

  free_mem = hv_info.get("memory_free", None)
  if not isinstance(free_mem, int):
    raise errors.OpPrereqError("Can't compute free memory on node %s, result"
                               " was '%s'" % (node, free_mem),
                               errors.ECODE_ENVIRON)
  if requested > free_mem:
    raise errors.OpPrereqError("Not enough memory on node %s for %s:"
                               " needed %s MiB, available %s MiB" %
                               (node, reason, requested, free_mem),
                               errors.ECODE_NORES)
  return free_mem


def CheckInstanceBridgesExist(lu, instance, node=None):
  """Check that the brigdes needed by an instance exist.

  """
  if node is None:
    node = instance.primary_node
  CheckNicsBridgesExist(lu, instance.nics, node)


def CheckNicsBridgesExist(lu, target_nics, target_node):
  """Check that the brigdes needed by a list of nics exist.

  """
  cluster = lu.cfg.GetClusterInfo()
  paramslist = [cluster.SimpleFillNIC(nic.nicparams) for nic in target_nics]
  brlist = [params[constants.NIC_LINK] for params in paramslist
            if params[constants.NIC_MODE] == constants.NIC_MODE_BRIDGED]
  if brlist:
    result = lu.rpc.call_bridges_exist(target_node, brlist)
    result.Raise("Error checking bridges on destination node '%s'" %
                 target_node, prereq=True, ecode=errors.ECODE_ENVIRON)


def CheckNodeHasOS(lu, node, os_name, force_variant):
  """Ensure that a node supports a given OS.

  @param lu: the LU on behalf of which we make the check
  @param node: the node to check
  @param os_name: the OS to query about
  @param force_variant: whether to ignore variant errors
  @raise errors.OpPrereqError: if the node is not supporting the OS

  """
  result = lu.rpc.call_os_get(node, os_name)
  result.Raise("OS '%s' not in supported OS list for node %s" %
               (os_name, node),
               prereq=True, ecode=errors.ECODE_INVAL)
  if not force_variant:
    _CheckOSVariant(result.payload, os_name)


def _CheckOSVariant(os_obj, name):
  """Check whether an OS name conforms to the os variants specification.

  @type os_obj: L{objects.OS}
  @param os_obj: OS object to check
  @type name: string
  @param name: OS name passed by the user, to check for validity

  """
  variant = objects.OS.GetVariant(name)
  if not os_obj.supported_variants:
    if variant:
      raise errors.OpPrereqError("OS '%s' doesn't support variants ('%s'"
                                 " passed)" % (os_obj.name, variant),
                                 errors.ECODE_INVAL)
    return
  if not variant:
    raise errors.OpPrereqError("OS name must include a variant",
                               errors.ECODE_INVAL)

  if variant not in os_obj.supported_variants:
    raise errors.OpPrereqError("Unsupported OS variant", errors.ECODE_INVAL)


def BuildDiskLogicalIDEnv(template_name, idx, disk):
  if template_name == constants.DT_PLAIN:
    vg, name = disk.logical_id
    ret = {
      "INSTANCE_DISK%d_VG" % idx : vg,
      "INSTANCE_DISK%d_ID" % idx : name
      }
  elif template_name in (constants.DT_FILE, constants.DT_SHARED_FILE):
    file_driver, name = disk.logical_id
    ret = {
      "INSTANCE_DISK%d_DRIVER" % idx : file_driver,
      "INSTANCE_DISK%d_ID" % idx : name
      }
  elif template_name == constants.DT_BLOCK:
    block_driver, adopt = disk.logical_id
    ret = {
      "INSTANCE_DISK%d_DRIVER" % idx : block_driver,
      "INSTANCE_DISK%d_ID" % idx : adopt
      }
  elif template_name == constants.DT_RBD:
    rbd, name = disk.logical_id
    ret = {
      "INSTANCE_DISK%d_DRIVER" % idx : rbd,
      "INSTANCE_DISK%d_ID" % idx : name
      }
  elif template_name == constants.DT_EXT:
    provider, name = disk.logical_id
    ret = {
      "INSTANCE_DISK%d_PROVIDER" % idx : provider,
      "INSTANCE_DISK%d_ID" % idx : name
      }
  elif template_name == constants.DT_DRBD8:
    pnode, snode, port, pmin, smin, _ = disk.logical_id
    data, meta = disk.children
    data_vg, data_name = data.logical_id
    meta_vg, meta_name = meta.logical_id
    ret = {
      "INSTANCE_DISK%d_PNODE" % idx : pnode,
      "INSTANCE_DISK%d_SNODE" % idx : snode,
      "INSTANCE_DISK%d_PORT" % idx : port,
      "INSTANCE_DISK%d_PMINOR" % idx : pmin,
      "INSTANCE_DISK%d_SMINOR" % idx : smin,
      "INSTANCE_DISK%d_DATA_VG" % idx : data_vg,
      "INSTANCE_DISK%d_DATA_ID" % idx : data_name,
      "INSTANCE_DISK%d_META_VG" % idx : meta_vg,
      "INSTANCE_DISK%d_META_ID" % idx : meta_name,
      }
  elif template_name == constants.DT_DISKLESS:
    ret = {}

  ret.update({
    "INSTANCE_DISK%d_TEMPLATE_NAME" % idx: template_name
    })

  return ret
